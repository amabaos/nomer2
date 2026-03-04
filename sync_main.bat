@echo off
setlocal

echo [1/5] checkout main
git checkout main
if errorlevel 1 goto :fail

echo [2/5] pull origin main
git pull origin main
if errorlevel 1 goto :fail

echo [3/5] add changes
git add -A
if errorlevel 1 goto :fail

echo [4/5] commit if needed
git diff --cached --quiet
if %errorlevel%==0 (
  echo Nothing to commit
) else (
  git commit -m "sync update"
  if errorlevel 1 goto :fail
)

echo [5/5] push origin main
git push origin main
if errorlevel 1 goto :fail

echo Done.
exit /b 0

:fail
echo Failed.
exit /b 1
