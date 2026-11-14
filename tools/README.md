# Power BI validation tooling

Two small .NET console apps that make the Power BI project in `powerbi/` checkable without a
human looking at the screen. Both are development tools, not part of the platform.

They exist because Power BI Desktop is a GUI: it reports a broken model as a character offset in
an unnamed expression, and it reports a wrong measure as a number in a card. Neither is a test.

## `tmdl-validate` — check the semantic model offline

Deserialises the TMDL `definition` folder with the Tabular Object Model and prints the structure.
Catches syntax errors, empty DAX, and malformed tables before Desktop is opened.

```powershell
dotnet run --project tools/tmdl-validate -- powerbi/Olist.SemanticModel/definition
dotnet run --project tools/tmdl-validate -- powerbi/Olist.SemanticModel/definition --dump
```

`--dump` prints every M expression and partition query verbatim, with tabs and carriage returns
made visible. That is how the triple-backtick bug in `model.tmdl` was found: the fences were
being kept as the first and last lines of the M text.

## `as-query` — talk to the running model

Power BI Desktop hosts a local tabular instance while a project is open. This connects to it over
ADOMD so the model can be inspected and its measures evaluated.

```powershell
# find the port
Get-Content "$env:LOCALAPPDATA\Microsoft\Power BI Desktop\AnalysisServicesWorkspaces\*\Data\msmdsrv.port.txt"

dotnet run --project tools/as-query -- <port>                    # tables, measures, relationships
dotnet run --project tools/as-query -- <port> --dax "EVALUATE ROW(\"n\", [Total Orders])"
dotnet run --project tools/as-query -- <port> --dax @query.dax   # from a file, avoids shell quoting
```

Prefer `@file.dax`. PowerShell mangles the escaped quotes in an inline DAX string.

## The acceptance test

`scripts/validate_powerbi_model.py` uses `as-query` to evaluate all 20 catalogue measures inside
the live model and compares them to the expected values in `docs/powerbi-measures.md`.

```powershell
python scripts/validate_powerbi_model.py
```

It requires the project to be open in Desktop **and the data to be loaded**. A PBIP project opens
with the model definition but no cached data, so the first run after opening reports every measure
as blank. Click Refresh in the ribbon and wait for the load before validating.

`scripts/validate_powerbi_measures.py` is the same check against BigQuery directly, and needs no
Desktop instance. Run both: the first proves the DAX is right, the second proves the warehouse is.

## Requirements

- .NET SDK 9 or later
- Power BI Desktop (for `as-query` only)
- The `Microsoft.AnalysisServices.*` NuGet packages, restored automatically on first build
