// Creates (or updates) the "ai-event-processor" pipeline job: Jenkinsfile from
// the snapshot repo mounted at REPO_URL (default file:///repo), branch ci-snapshot.
import jenkins.model.Jenkins
import hudson.model.BooleanParameterDefinition
import hudson.model.ParametersDefinitionProperty
import hudson.model.StringParameterDefinition
import hudson.plugins.git.BranchSpec
import hudson.plugins.git.GitSCM
import org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition
import org.jenkinsci.plugins.workflow.job.WorkflowJob

def j = Jenkins.get()
def name = 'ai-event-processor'
def url = System.getenv('REPO_URL') ?: 'file:///repo'
def job = j.getItem(name) ?: j.createProject(WorkflowJob, name)
def scm = new GitSCM(
    GitSCM.createRepoList(url, null), [new BranchSpec('*/ci-snapshot')], null, null, [])
def definition = new CpsScmFlowDefinition(scm, 'Jenkinsfile')
definition.setLightweight(false)
job.setDefinition(definition)
// Same parameters as the Jenkinsfile, so buildWithParameters works on build #1.
job.removeProperty(ParametersDefinitionProperty)
job.addProperty(new ParametersDefinitionProperty([
    new BooleanParameterDefinition('RUN_LOAD_TEST', false, 'Run the JMeter load test'),
    new StringParameterDefinition('LOAD_RATE', '2000', 'Target events/s'),
    new StringParameterDefinition('LOAD_DURATION', '60', 'Load test duration, seconds'),
]))
job.setDescription('CI for the AI-Driven Event Processing System (Jenkinsfile at repo root).')
job.save()
