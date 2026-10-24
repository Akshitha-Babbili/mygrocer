
pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
    }

    stages {
        stage('Checkout') {
            steps {
                echo 'MyGrocer source code checked out from GitHub'
                bat 'git rev-parse --short HEAD'
            }
        }

        stage('Validate Project Files') {
            steps {
                bat '''
                    if not exist index.html exit /b 1
                    if not exist register.html exit /b 1
                    if not exist dashboard.html exit /b 1
                    if not exist kitchen.html exit /b 1
                    echo All required MyGrocer HTML pages exist.
                '''
            }
        }

        stage('Archive Project') {
            steps {
                archiveArtifacts artifacts: '*.html',
                                 fingerprint: true
            }
        }
    }

    post {
        success {
            echo 'MyGrocer CI pipeline completed successfully.'
        }

        failure {
            echo 'MyGrocer CI pipeline failed. Check the stage logs.'
        }
    }
}
