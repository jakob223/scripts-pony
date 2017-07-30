import tempfile
import subprocess

import vhosts

ACCOUNT_KEY = '/afs/athena.mit.edu/contrib/scripts/REPLACEME/account.key'  # TODO: make an account key
ACME_DIR = '/afs/athena.mit/edu/contrib/scripts/REPLACEME'  # TODO: decide an acme_dir or another way of storing responses to challenges


def request_and_install(locker, hostname, aliases):
    """ request_and_install contacts the Let's Encrypt servers, performs an authentication,
        requests a certificate for @hostname, and installs it in LDAP."""
    csr_req_cmd = ['/bin/sudo', '/etc/pki/tls/gencsr-pony',locker,hostname]

    for alias in aliases:
        csr_req_cmd.append(alias)

    csr_req = subprocess.Popen(csr_req_cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    out, err = csr_req.communicate()
    if csr_req.returncode:
        raise vhosts.UserError("CSR Request Failed")
    else:
        csr_contents = out
        csr_file = tempfile.NamedTemporaryFile()
        # write csr_contents to csr_file
        csr_file.write(csr_contents)

        # call acme_tiny.py with the CSR
        cert = acme_tiny.get_crt(ACCOUNT_KEY, csr_file.name(), ACME_DIR, log=acme_tiny.LOGGER, CA=acme_tiny.DEFAULT_CA)
        csr_file.close() 

        # download the intermediate cert
        intermediate_cert_location = "https://letsencrypt.org/certs/lets-encrypt-x3-cross-signed.pem"
        intermediate_cert = urlopen(intermediate_cert_location).read()
        certs = cert + "\n" + intermediate_cert
        
        # import the cert into scripts
        # exceptions will bubble up and be caught by caller.
        vhosts.set_cert(locker, hostname, importcert)
           

    