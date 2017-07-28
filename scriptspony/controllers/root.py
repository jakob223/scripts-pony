# -*- coding: utf-8 -*-
"""Main Controller"""

from tg import expose, flash, require, url, request, redirect, override_template
from pylons.i18n import ugettext as _, lazy_ugettext as l_
import pylons

from scriptspony.lib.base import BaseController
from scriptspony.model import DBSession, metadata
from scriptspony.model.user import UserInfo
from scriptspony.controllers.error import ErrorController

from sqlalchemy.orm.exc import NoResultFound

from decorator import decorator
import subprocess
import cgi
import tempfile
from urllib2 import urlopen

from scripts import auth
from .. import mail,vhosts,acme_tiny
from ..model import queue

__all__ = ['RootController']

# Not in auth because it depends on TG
@decorator
def scripts_team_only(func,*args,**kw):
    if not auth.on_scripts_team():
        flash("You are not authorized for this area!")
        redirect('/')
    else:
        return func(*args,**kw)

class RootController(BaseController):
    """
    The root controller for the ScriptsPony application.
    
    All the other controllers and WSGI applications should be mounted on this
    controller. For example::
    
        panel = ControlPanelController()
        another_app = AnotherWSGIApplication()
    
    Keep in mind that WSGI applications shouldn't be mounted directly: They
    must be wrapped around with :class:`tg.controllers.WSGIAppController`.
    
    """
    
    error = ErrorController()

    @expose('scriptspony.templates.index')
    def index(self,locker=None,sudo=False):
        """Handle the front-page."""
        if locker is not None and pylons.request.response_ext:
            locker += pylons.request.response_ext
        
        olocker = locker
        hosts = None
        user = auth.current_user()
        https = auth.is_https()
        # Find or create the associated user info object.
        # TODO: is there a find_or_create sqlalchemy method?
        if user:
            if sudo and auth.on_scripts_team():
                #override_template(self.index, 'mako:scripts.templates.confirm')
                #return dict(action=url('/new/'+locker),title="Really use Scripts Team bits to request a hostname as locker '%s'?"%locker,question="Only do this in response to a user support request, and after checking to make sure that the request comes from someone authorized to make requests for the locker.",
                #           backurl=url('/index'))
                redirect('/new/%s?confirmed=true'%locker)
            try:
                user_info = DBSession.query(UserInfo).filter(UserInfo.user==user).one()
            except NoResultFound:
                user_info = UserInfo(user)
                DBSession.add(user_info)
        else:
            user_info = None

        if user is not None:
            if locker is None:
                locker = user
            try:
                hosts = vhosts.list_vhosts(locker)
                hosts.sort(key=lambda k:k[0])
            except auth.AuthError,e:
                flash(e.message)
                # User has been deauthorized from this locker
                if locker in user_info.lockers:
                    user_info.lockers.remove(locker)
                    DBSession.add(user_info)
                if olocker is not None:
                    return self.index()
                else:
                    return dict(hosts={},locker=locker,user_info=user_info)
            else:
                # Append locker to the list in user_info if it's not there
                if not locker in user_info.lockers:
                    user_info.lockers.append(locker)
                    user_info.lockers.sort()
                    DBSession.add(user_info)
                    flash('You can administer the "%s" locker.' % locker)
        return dict(hosts=hosts, locker=locker, user_info=user_info,
                    https=https)

    @expose('scriptspony.templates.edit')
    def edit(self,locker,hostname,path=None,token=None,alias=''):
        if pylons.request.response_ext:
            hostname += pylons.request.response_ext
        if path is not None:
            if token != auth.token():
                flash("Invalid token!")
            else:
                try:
                    vhosts.set_path(locker,hostname,path)
                except vhosts.UserError,e:
                    flash(e.message)
                else:
                    flash("Host '%s' reconfigured."%hostname)
                    redirect('/index/'+locker)
            _,aliases=vhosts.get_vhost_info(locker,hostname)
        else:
            if alias:
                if token != auth.token():
                    flash("Invalid token!")
                else:
                    try:
                        vhosts.add_alias(locker,hostname,alias)
                    except vhosts.UserError,e:
                        flash(e.message)
                    else:
                        flash("Alias '%s' added to hostname '%s'."
                              % (alias,hostname))
                        redirect('/index/'+locker)
            try:
                path,aliases=vhosts.get_vhost_info(locker,hostname)
            except vhosts.UserError,e:
                flash(e.message)
                redirect('/index/'+locker)
        return dict(locker=locker, hostname=hostname,
                    path=path, aliases=aliases, alias=alias)

    
    @expose()
    def request_le(self,locker,hostname,token=None, **kwargs):
        if pylons.request.response_ext:
            hostname += pylons.request.response_ext

        if token is not None:
            if token != auth.token():
                flash("Invalid token!")
                redirect('/index/'+locker)
            else:
               try:
                   path,aliases=vhosts.get_vhost_info(locker,hostname)
               except vhosts.UserError,e:
                   flash(e.message)
                   redirect('/index/'+locker)
               else:
                   if hostname.endswith('.mit.edu') and not auth.on_scripts_team():
                       flash("You can't request a CSR for an MIT.edu host - please contact the scripts team for help.")
                       redirect('/index/'+locker)
                   
                   csr_req_cmd = ['/bin/sudo', '/etc/pki/tls/gencsr-pony',locker,hostname]
                   for arg,value in kwargs:
                       if arg[:5] == 'alias':
                           if value in aliases:
                               csr_req_cmd.append(value)
                   csr_req = subprocess.Popen(csr_req_cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                   out, err = csr_req.communicate()
                   if csr_req.returncode:
                       flash("CSR request failed.")
                       redirect("/index/"+locker)
                   else:
                       csr_contents = out
                       csr_file = tempfile.NamedTemporaryFile()
                       # write csr_contents to csr_file
                       csr_file.write(csr_contents)

                       # TODO: make an account key
                       account_key = '/afs/athena.mit.edu/contrib/scripts/REPLACEME/account.key'
                       acme_dir = '/afs/athena.mit/edu/contrib/scripts/REPLACEME' # to be eliminated or decided upon
                       # call acme_tiny.py with the CSR
                       cert = acme_tiny.get_crt(account_key, csr_file.name(), acme_dir, log=acme_tiny.LOGGER, CA=acme_tiny.DEFAULT_CA):
                       csr_file.close() 

                       # TODO: download the intermediate cert
                       intermediate_cert_location = "https://letsencrypt.org/certs/lets-encrypt-x3-cross-signed.pem"
                       intermediate_cert = urlopen(intermediate_cert_location).read()
                       certs = cert + "\n" + intermediate_cert
                       # parse the certificate (vhostcert import )
                       # TODO: pull this code out into somewhere so it isn't repeated twice
                       importcert = subprocess.Popen(['/afs/athena.mit.edu/contrib/scripts/sbin/vhostcert', 'import'],stdout=subprocess.PIPE,stderr=subprocess.PIPE, stdin=subprocess.PIPE)
                       certstring, err = importcert.communicate(input=certs.strip())
                       if importcert.returncode:
                           flash("Error installing cert, is it malformed: "+err)
                           redirect('/index/'+locker)
                       # make sure it is the right cert
                       # TODO: move this somewhere scripts-ey. Source is in this repo.
                       check_certs_cmd = ['/afs/athena.mit.edu/user/j/a/jakobw/arch/common/bin/verify-certs','-hostname='+hostname]
                       check_certs = subprocess.Popen(check_certs_cmd, stdout=subprocess.PIPE,stderr=subprocess.PIPE,stdin=subprocess.PIPE)
                       out, err = check_certs.communicate(input=certs)
                       if check_certs.returncode:
                          flash("Certificate chain invalid: "+err)
                          redirect('/index/' + locker)
                       
                       # put it into LDAP
                       vhosts.set_cert(locker, hostname, importcert)
                       flash("Added certificate for %s - it will become active within an hour." % hostname)
                       redirect('/index/' + locker)

                       # TODO: separate all this into a function that can also be called by the cronjob
        else:
            flash("This endpoint requires POST parameters")
            redirect('/index/'+locker)


    @expose('scriptspony.templates.request_cert')
    def request_cert(self,locker,hostname,token=None,certificate=None, **kwargs):
        if pylons.request.response_ext:
            hostname += pylons.request.response_ext
            
        show_only_csr = False
        csr_success = False
        csr_contents = ""
        if token is not None:
            if token != auth.token():
                flash("Invalid token!")
            else:
                if certificate is not None: # form is for certificate
                    # parse the certificate (vhostcert import )
                    importcert = subprocess.Popen(['/afs/athena.mit.edu/contrib/scripts/sbin/vhostcert', 'import'],stdout=subprocess.PIPE,stderr=subprocess.PIPE, stdin=subprocess.PIPE)
                    certstring, err = importcert.communicate(input=certificate.strip())
                    if importcert.returncode:
                        flash("Error installing cert, is it malformed: "+err)
                        redirect('/index/'+locker)
                    # make sure it is the right cert
                    # TODO: move this somewhere scripts-ey. Source is in this repo.
                    check_certs_cmd = ['/afs/athena.mit.edu/user/j/a/jakobw/arch/common/bin/verify-certs','-hostname='+hostname]
                    check_certs = subprocess.Popen(check_certs_cmd, stdout=subprocess.PIPE,stderr=subprocess.PIPE,stdin=subprocess.PIPE)
                    out, err = check_certs.communicate(input=certificate)
                    if check_certs.returncode:
                       flash("Certificate chain invalid: "+err)
                       redirect('/index/' + locker)
                    
                    # put it into LDAP
                    vhosts.set_cert(locker, hostname, importcert)
                    flash("Added certificate for %s - it will become active within an hour." % hostname)
                else: # form is for CSR generation
                    try:
                        path,aliases=vhosts.get_vhost_info(locker,hostname)
                    except vhosts.UserError,e:
                        flash(e.message)
                        redirect('/index/'+locker)
                    else:
                        if hostname.endswith('.mit.edu') and not auth.on_scripts_team():
                            flash("You can't request a CSR for an MIT.edu host - please contact the scripts team for help.")
                            redirect('/index/'+locker)
                        
                        csr_req_cmd = ['/bin/sudo', '/etc/pki/tls/gencsr-pony',locker,hostname]
                        for arg,value in kwargs:
                            if arg[:5] == 'alias':
                                if value in aliases:
                                    csr_req_cmd.append(value)
                        csr_req = subprocess.Popen(csr_req_cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                        out, err = csr_req.communicate()
                        if csr_req.returncode:
                            # TODO: determine if this is a security risk, if err could somehow contain privatekey material
                            csr_success = False
                            csr_contents = err
                        else:
                            csr_success = True
                            csr_contents = out
                        show_only_csr = True
            _,aliases=vhosts.get_vhost_info(locker,hostname)
        else:
            try:
                _,aliases=vhosts.get_vhost_info(locker,hostname)
            except vhosts.UserError,e:
                flash(e.message)
                redirect('/index/'+locker)
        return dict(locker=locker, hostname=hostname,
                    aliases=aliases,show_only_csr=show_only_csr,
                    csr_success=csr_success,csr_contents=csr_contents)



    @expose('scriptspony.templates.new')
    def new(self,locker,hostname='',path='',desc='',token=None,
            confirmed=False,personal_ok=False,requestor=None):
        personal = locker == auth.current_user()
        if confirmed:
            auth.scripts_team_sudo()
        else:
            requestor = None
        if pylons.request.response_ext:
            locker += pylons.request.response_ext
        if hostname:
            if token != auth.token():
                flash("Invalid token!")
            elif not desc and not confirmed:
                flash("Please specify the purpose of this hostname.")
            elif requestor is not None and not requestor.strip():
                flash("Please specify requestor.")
            elif personal and not personal_ok:
                flash("Please acknowledge that your hostname will be served from your personal locker and will be deleted when you leave MIT.")
            else:
                try:
                    status = vhosts.request_vhost(locker,hostname,path,
                                                  user=requestor,
                                                  desc=desc)
                except vhosts.UserError,e:
                    flash(e.message)
                else:
                    flash(status)
                    if confirmed:
                        redirect('/queue')
                    else:
                        redirect('/index/'+locker)
        else:
            try:
                auth.validate_locker(locker,sudo_ok=True)
            except auth.AuthError,e:
                flash(e.message)
                redirect('/')

        return dict(locker=locker,hostname=hostname,path=path,desc=desc,
                    confirmed=confirmed,personal=personal)

    @expose('scriptspony.templates.queue')
    @scripts_team_only
    def queue(self,**kw):
        all = ('open','moira','dns','resolved','rejected')
        if len(kw) <= 0:
            kw = dict(open='1',moira='1',dns='1')
        query = queue.Ticket.query
        for k in all:
            if k not in kw:
                query = query.filter(queue.Ticket.state != k)
        return dict(tickets=query.all(),all=all,included=kw)

    @expose('scriptspony.templates.ticket')
    @scripts_team_only
    def ticket(self,id):
        return dict(tickets=[queue.Ticket.get(int(id))])

    @expose('scriptspony.templates.message')
    @scripts_team_only
    def approve(self,id,subject=None,body=None,token=None,silent=False):
        t = queue.Ticket.get(int(id))
        if t.state != 'open':
            flash("This ticket's not open!")
            redirect('/ticket/%s'%id)
        if t.rtid is None:
            flash("This ticket has no RT ID!")
            redirect('/ticket/%s'%id)
        if subject and body:
            if token != auth.token():
                flash("Invalid token!")
            else:
                try:
                    vhosts.actually_create_vhost(t.locker,t.hostname,t.path)
                except vhosts.UserError,e:
                    flash(e.message)
                else:
                    if not silent:
                        # Send mail and records it as an event
                        mail.send_comment(subject,body,t.id,t.rtid,
                                          auth.current_user(),'accounts-internal')
                        t.addEvent(type='mail',state='moira',target='accounts-internal',
                                   subject=subject,body=body)
                        flash("Ticket approved; mail sent to accounts-internal.")
                    else:
                        mail.send_comment(subject,"Ticket approved silently.\n\n"+body,t.id,t.rtid,
                                          auth.current_user())
                        t.addEvent(type='mail',state='dns',
                                   target='us')
                        flash("Ticket approved silently.")
                    redirect('/queue')
        short = t.hostname[:-len('.mit.edu')]
        assert t.hostname[0] != '-'
        stella = subprocess.Popen(["/usr/bin/stella",t.hostname],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        out,err = stella.communicate()
        return dict(tickets=[t],action=url('/approve/%s'%id),
                    subject="scripts-vhosts CNAME request: %s"%short,
                    body="""Hi accounts-internal,

At your convenience, please make %(short)s an alias of scripts-vhosts.

stella scripts-vhosts -a %(short)s

Thanks!
-%(first)s
SIPB Scripts Team

/set status=stalled
""" % dict(short=short,first=auth.first_name()),
      help_text_html="<p>Make sure the host name is not being used:</p><pre>$ stella %s\n%s\n%s</pre><p>If it's DELETED, you need to forward explicit confirmation that it's OK to reuse (from owner/contact/billing contact, or rccsuper for dorms, or a FSILG's net contact, or similar).</p>" % (cgi.escape(t.hostname),cgi.escape(out),cgi.escape(err)),
                    extra_buttons={'silent':'Approve without mailing accounts-internal'})
            
    @expose('scriptspony.templates.message')
    @scripts_team_only
    def reject(self,id,subject=None,body=None,token=None,silent=False):
        t = queue.Ticket.get(int(id))
        if t.state != 'open':
            flash("This ticket's not open!")
            redirect('/ticket/%s'%id)
        if t.rtid is None:
            flash("This ticket has no RT ID!")
            redirect('/ticket/%s'%id)
        if (subject and body) or silent:
            if token != auth.token():
                flash("Invalid token!")
            else:
                # Send mail and records it as an event
                if not silent:
                    mail.send_correspondence(subject,body,
                                             t.rtid, auth.current_user())
                    t.addEvent(type=u'mail',state=u'rejected',target=u'user',
                               subject=subject,body=body)
                    flash("Ticket rejected; mail sent to user.")
                else:
                    mail.send_comment(subject,"Ticket rejected silently.\n\n"+body,t.id,t.rtid,
                                      auth.current_user())
                    t.addEvent(type=u'mail',state=u'rejected',
                               target=u'rt', subject=subject,body=body)
                    flash("Ticket rejected silently.")
                redirect('/queue')
        return dict(tickets=[t],action=url('/reject/%s'%id),
                    subject="Re: Request for hostname %s"%t.hostname,
                    body="""Hello,

Unfortunately, the hostname %(hostname)s is not available.  You can go to http://pony.scripts.mit.edu/ to request a different one.

Sorry for the inconvenience,
-%(first)s

/set status=rejected
""" % dict(hostname=t.hostname,first=auth.first_name()),
                    submit='Send to %s' % t.requestor,
                    extra_buttons={'silent':'Send as Comment'})
