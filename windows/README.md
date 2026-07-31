# Windows deployment — Task Scheduler

Optional. The bot runs perfectly well started manually (`py -m
bot.discord_bot`, per the root `README.md`'s own "Running" section) —
these three scripts exist only to make it start automatically without
you having to open a terminal every time you log on to Windows.

Nothing here is required to develop or manually run the bot. Nothing
here is imported by, or changes the behavior of, any part of the
application itself — this is deployment tooling, not application code.

## Files

- **`run_bot.ps1`** — the actual launcher. Derives the project root
  from its own location (never a hardcoded path), prefers `.venv` if
  one exists, falls back to the `py` launcher otherwise, and appends
  timestamped output to `..\logs\bot.log`.
- **`install_task.ps1`** — registers a Windows Scheduled Task that
  runs `run_bot.ps1` whenever you log on.
- **`uninstall_task.ps1`** — removes that Scheduled Task.

None of these three scripts contain a token, a path specific to any
one person's user account, or anything else that shouldn't be shared —
they're safe to commit.

## Install

Open PowerShell **in the `windows\` folder** (or use its full path)
and run:

```powershell
.\install_task.ps1
```

This registers a Scheduled Task named `AICoachKeyholderBot` that
starts the next time you log on. To give it a different name (e.g. if
you're running more than one instance):

```powershell
.\install_task.ps1 -TaskName "MyBotName"
```

If PowerShell refuses to run the script at all
(`... cannot be loaded because running scripts is disabled...`), that's
Windows' default script-execution policy, not a problem with this
script — either run PowerShell once as:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_task.ps1
```

or set your own execution policy for your user account
(`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`) if you're
comfortable with that as a general setting.

## Start it right now (without logging off/on)

```powershell
Start-ScheduledTask -TaskName "AICoachKeyholderBot"
```

## Stop it

```powershell
Stop-ScheduledTask -TaskName "AICoachKeyholderBot"
```

This stops the current run; the Scheduled Task itself remains
registered and will start again next logon (or the next time you run
`Start-ScheduledTask`).

## Uninstall

```powershell
.\uninstall_task.ps1
```

Removes the Scheduled Task. If a launched bot process happens to still
be running at that moment, this does not kill it — check Task Manager
if you need to stop it immediately too.

## Check logs

```powershell
Get-Content ..\logs\bot.log -Tail 50 -Wait
```

(`-Wait` follows the file live, like `tail -f`.) `logs\` is
git-ignored — see the root `.gitignore`; this directory is created
automatically by `run_bot.ps1` the first time it runs.

You can also check the Task itself from Task Scheduler's own GUI
(`taskschd.msc`) or:

```powershell
Get-ScheduledTaskInfo -TaskName "AICoachKeyholderBot"
```

## Manual runs during development

Nothing above stops you from still running the bot directly, exactly
as `README.md`'s "Running" section describes:

```powershell
py -m bot.discord_bot
```

`run_bot.ps1` itself can also be run directly, any time, without the
Scheduled Task installed at all — it's just `py -m bot.discord_bot`
with path resolution and logging wrapped around it.

## AtLogOn vs. independent of logon — and why AtLogOn is the default

Windows Task Scheduler supports two fundamentally different modes for
who a task runs as:

- **"Run only when user is logged on" (`AtLogOn`, this project's
  default):** the task starts using your own already-active Windows
  session. No password is stored anywhere — Task Scheduler doesn't
  need one, since it's reusing your existing logged-on session. The
  bot stops if you log off. Simpler, and there is no credential of any
  kind sitting in Task Scheduler's own storage for anything to find.
- **"Run whether user is logged on or not":** the task can start even
  before anyone logs in (e.g. right after a reboot, unattended).
  Requires Task Scheduler to store a **password** for the account it
  runs as, in its own protected credential store — real, if modest,
  additional attack surface, and one more secret that has to be
  managed and rotated if it's ever suspected of being compromised.

**Default here: `AtLogOn`.** For a personal bot on a personal Windows
machine — exactly this project's actual situation — the practical
downside (the bot isn't running before you've logged in) is a small
price for not having a stored Windows credential anywhere. If this
project ever runs unattended on a machine nobody regularly logs into
(a dedicated home server, for instance), "whether user is logged on or
not" becomes the more appropriate choice — that's a deliberate,
separate decision to make at that point, not something to default into
here.

## Auto-restart after a crash

`install_task.ps1` configures Task Scheduler's own restart policy (up
to 3 attempts, 1 minute apart) — if the bot process exits unexpectedly,
Windows restarts it automatically without any custom supervisor script
needed. `run_bot.ps1` also sets an unlimited execution time limit
(`ExecutionTimeLimit`) — Task Scheduler's own default is 3 days, which
would otherwise silently kill a long-running bot process that Task
Scheduler assumes is a stuck batch job.
