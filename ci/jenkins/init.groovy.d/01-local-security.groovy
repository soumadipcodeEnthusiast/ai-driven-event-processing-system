// Local-only Jenkins (bound to 127.0.0.1 by run-local.sh): no login, 4 executors.
import jenkins.model.Jenkins
import hudson.security.AuthorizationStrategy
import hudson.security.SecurityRealm

def j = Jenkins.get()
j.setSecurityRealm(SecurityRealm.NO_AUTHENTICATION)
j.setAuthorizationStrategy(AuthorizationStrategy.UNSECURED)
j.setNumExecutors(4)
j.save()
