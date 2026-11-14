// Offline TMDL validator.
//
// Power BI Desktop is the only thing that can fully validate a PBIP project, but it needs a GUI
// and reports failures as a character offset in an unnamed expression. This tool deserialises a
// TMDL `definition` folder with the Tabular Object Model and reports the structure, so a semantic
// model can be checked — and its M expressions inspected verbatim — before Desktop is opened.
//
// Usage:
//   dotnet run --project tools/tmdl-validate -- <definition-folder> [--dump]

using Microsoft.AnalysisServices.Tabular;

if (args.Length == 0)
{
    Console.Error.WriteLine("usage: tmdl-validate <definition-folder> [--dump]");
    return 2;
}

var path = Path.GetFullPath(args[0]);
if (!Directory.Exists(path))
{
    Console.Error.WriteLine($"not a directory: {path}");
    return 2;
}

try
{
    var database = TmdlSerializer.DeserializeDatabaseFromFolder(path);
    Console.WriteLine($"OK  database '{database.Name}' (compatibility {database.CompatibilityLevel})");

    var model = database.Model;
    Console.WriteLine($"    tables:        {model.Tables.Count}");
    Console.WriteLine($"    relationships: {model.Relationships.Count}");

    var measureCount = 0;
    foreach (var table in model.Tables)
    {
        var measures = table.Measures.Count;
        measureCount += measures;
        Console.WriteLine(
            $"    - {table.Name,-20} columns={table.Columns.Count,3}  measures={measures,3}  partitions={table.Partitions.Count}");
    }
    Console.WriteLine($"    total measures: {measureCount}");

    if (args.Length > 1 && args[1] == "--dump")
    {
        foreach (var e in model.Expressions)
        {
            Console.WriteLine($"--- expression '{e.Name}' ---");
            Console.WriteLine(e.Expression.Replace("\t", "<TAB>").Replace("\r", "<CR>"));
            Console.WriteLine("--- end ---");
        }
        foreach (var t in model.Tables)
        {
            foreach (var p in t.Partitions)
            {
                if (p.Source is MPartitionSource mps)
                {
                    Console.WriteLine($"--- partition '{t.Name}' ---");
                    Console.WriteLine(mps.Expression.Replace("\t", "<TAB>").Replace("\r", "<CR>"));
                    Console.WriteLine("--- end ---");
                }
            }
        }
    }

    // Surface anything TOM considers invalid without needing a live server.
    var errors = 0;
    foreach (var table in model.Tables)
    {
        foreach (var column in table.Columns)
        {
            if (string.IsNullOrWhiteSpace(column.Name))
            {
                Console.Error.WriteLine($"ERROR {table.Name}: column with no name");
                errors++;
            }
        }
        foreach (var measure in table.Measures)
        {
            if (string.IsNullOrWhiteSpace(measure.Expression))
            {
                Console.Error.WriteLine($"ERROR {table.Name}[{measure.Name}]: empty DAX expression");
                errors++;
            }
        }
    }

    if (errors > 0)
    {
        Console.Error.WriteLine($"{errors} structural error(s)");
        return 1;
    }

    Console.WriteLine("OK  no structural errors");
    return 0;
}
catch (Exception ex)
{
    Console.Error.WriteLine($"FAILED to deserialize TMDL: {ex.GetType().Name}");
    Console.Error.WriteLine(ex.Message);
    if (ex.InnerException is not null)
    {
        Console.Error.WriteLine($"  inner: {ex.InnerException.Message}");
    }
    return 1;
}
