<#
.SYNOPSIS
    Shared helpers for the Windows deployment scripts. Dot-sourced by
    both install_task.ps1 and run_bot.ps1 -- kept in one place so the
    interpreter-detection logic exists exactly once, not duplicated
    and potentially drifting between the two.
#>

function Find-PythonInterpreter {
    <#
    .SYNOPSIS
        Finds a real, working Python interpreter's absolute path.

    .DESCRIPTION
        Robust against a real, reported problem: on some Windows
        installs, neither `py` nor `python` resolve to a working
        interpreter at all -- Windows instead shows a "choose an app"/
        Microsoft Store prompt. This is the well-documented "App
        Execution Alias" behavior: Windows ships near-empty stub
        executables named python.exe/python3.exe under
        ...\WindowsApps\, which sit on PATH ahead of (or instead of) a
        real interpreter whenever no real Python has been installed
        through a path that registers itself ahead of that stub.
        `py`/`python` working perfectly on one machine and silently
        failing on another, for reasons that have nothing to do with
        this project's own code, is exactly what was observed and is
        the reason this function exists instead of this project's
        scripts simply calling `py`/`python` directly and hoping.

        Priority order:
          1. The project's own .venv\Scripts\python.exe, if one
             exists -- not just "a" Python, but the one with this
             project's actual dependencies installed.
          2. Every `py`/`python`/`python3` match on PATH (there can be
             more than one), in that order, skipping:
               - anything resolving under ...\WindowsApps\ (the known
                 stub location), and
               - anything implausibly small to be a real interpreter
                 (a genuine python.exe is at minimum several hundred
                 KB; the stub is only a few KB) -- a second,
                 independent check in case a stub ever turns up
                 somewhere else.

        Returns $null if nothing usable was found -- callers are
        expected to fail loudly rather than silently falling back to
        a bare `python`/`py` that might not work.

    .PARAMETER ProjectRoot
        The project's root directory (so the .venv check has
        somewhere to look).
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path $VenvPython) {
        return (Resolve-Path $VenvPython).Path
    }

    foreach ($name in @("py", "python", "python3")) {
        $candidates = Get-Command $name -All -ErrorAction SilentlyContinue
        foreach ($candidate in $candidates) {
            $path = $candidate.Source
            if ([string]::IsNullOrWhiteSpace($path)) {
                continue
            }
            if ($path -like "*\WindowsApps\*") {
                continue  # the known execution-alias stub location -- never a real interpreter
            }
            if (-not (Test-Path $path)) {
                continue
            }
            $sizeBytes = (Get-Item $path).Length
            if ($sizeBytes -lt 100000) {
                # A real python.exe is at minimum several hundred KB.
                # The WindowsApps stub is only a few KB -- this catches
                # an equivalent stub anywhere else too, not only the
                # known WindowsApps path.
                continue
            }
            return $path
        }
    }

    return $null
}
