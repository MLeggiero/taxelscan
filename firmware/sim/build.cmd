@echo off
rem Build the native conditioning simulation (Windows, MSVC).
rem Locates Visual Studio via vswhere rather than a hardcoded path.
setlocal enabledelayedexpansion

set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" set "VSWHERE=%ProgramFiles%\Microsoft Visual Studio\Installer\vswhere.exe"

set "VCVARS="
if exist "%VSWHERE%" (
  for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * ^
      -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 ^
      -property installationPath`) do set "VSPATH=%%i"
  if defined VSPATH set "VCVARS=!VSPATH!\VC\Auxiliary\Build\vcvars64.bat"
)

if not defined VCVARS (
  echo Could not locate Visual Studio.
  echo Install "Desktop development with C++" from the Visual Studio Installer,
  echo or run this from a Developer Command Prompt and it will use that instead.
  where cl.exe >nul 2>&1 || exit /b 1
  goto :compile
)
if not exist "!VCVARS!" (
  echo vcvars64.bat not found at !VCVARS!
  exit /b 1
)
call "!VCVARS!" >nul

:compile
if not exist obj mkdir obj
cl /nologo /O2 /EHsc /std:c++17 /W3 /wd4244 /wd4267 ^
   /I shim /I ..\taxelscan ^
   simtest.cpp ..\taxelscan\condition.cpp ^
   /Fe:sim.exe /Fo:obj\
exit /b %errorlevel%
