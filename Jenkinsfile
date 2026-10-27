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
                    if not exist app.py exit /b 1
                    if not exist requirements.txt exit /b 1
                    if not exist Dockerfile exit /b 1
                    if not exist templates\\index.html exit /b 1
                    if not exist templates\\register.html exit /b 1
                    if not exist templates\\dashboard.html exit /b 1
                    if not exist templates\\kitchen.html exit /b 1
                    if not exist templates\\shopping.html exit /b 1
                    echo All required MyGrocer application files exist.
                '''
            }
        }

        stage('Archive Project') {
            steps {
                archiveArtifacts artifacts: 'app.py,requirements.txt,Dockerfile,templates/*.html', fingerprint: true
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