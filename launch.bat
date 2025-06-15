@echo off
echo Starting GitLab CODEOWNERS tool container in the background...

docker run -d --rm --name gitlab-codeowners -p 5001:5001 gitlab-codeowners

timeout /t 1 /nobreak > nul

@REM echo Launching browser at http://localhost:5001
@REM :: The 'start' command opens the URL in your default browser
@REM start "" "http://localhost:5001"

docker logs -f gitlab-codeowners