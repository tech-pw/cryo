def getEnvName() {
    if (env.BRANCH_NAME == 'main') {
        return 'production'
    }
    else if (env.BRANCH_NAME == 'staging') {
        return 'staging'
    }
    else if (env.BRANCH_NAME == 'development') {
        return 'development'
    }
}

pipeline {
  agent {
  kubernetes {
    cloud 'kubernetes'
    inheritFrom 'cicd-v2'
    }
  }
  environment {
    serviceName = 'data-cryo'
    envName = getEnvName()
  }
  stages{
        stage('Starting-CICD') {
            steps {
                build wait: false, job: "/${env.envName}/${env.serviceName}", parameters: [string(name: 'ServiceName', value: "${env.serviceName}"), string(name: 'RepoUrl', value: "${GIT_URL}")]
            }
        }
    }
}