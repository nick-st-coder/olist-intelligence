// Query a live Power BI Desktop Analysis Services instance.
//
// Power BI Desktop hosts a local tabular instance while a project is open. This tool connects to
// it so the semantic model can be inspected and its measures evaluated without the GUI. It is the
// transport behind scripts/validate_powerbi_model.py.
//
// Usage:
//   dotnet run --project tools/as-query -- <port>                  # list tables and measures
//   dotnet run --project tools/as-query -- <port> --dax "<query>"  # run DAX
//   dotnet run --project tools/as-query -- <port> --dax @file.dax  # run DAX from a file
//
// The port is in
//   %LOCALAPPDATA%\Microsoft\Power BI Desktop\AnalysisServicesWorkspaces\*\Data\msmdsrv.port.txt

using Microsoft.AnalysisServices.AdomdClient;
using Microsoft.AnalysisServices.Tabular;

if (args.Length == 0)
{
    Console.Error.WriteLine("usage: as-query <port> [--dax \"EVALUATE ...\" | --dax @file.dax]");
    return 2;
}

var port = args[0];
var conn = $"Provider=MSOLAP;Data Source=localhost:{port};";

try
{
    using var server = new Server();
    server.Connect(conn);
    Console.WriteLine($"connected: {server.Version}");

    if (args.Length > 2 && args[1] == "--dax")
    {
        var dax = args[2];
        if (dax.StartsWith('@'))
        {
            dax = File.ReadAllText(dax[1..]);
        }

        foreach (Database d in server.Databases)
        {
            Console.WriteLine($"--- {d.Name} ---");
            using var adomd = new AdomdConnection($"Data Source=localhost:{port};Catalog={d.Name};");
            adomd.Open();
            using var cmd = new AdomdCommand(dax, adomd);
            using var reader = cmd.ExecuteReader();

            var cols = reader.FieldCount;
            for (var i = 0; i < cols; i++)
            {
                Console.Write($"{reader.GetName(i)}\t");
            }
            Console.WriteLine();

            while (reader.Read())
            {
                for (var i = 0; i < cols; i++)
                {
                    Console.Write($"{reader.GetValue(i)}\t");
                }
                Console.WriteLine();
            }
        }
        return 0;
    }

    foreach (Database db in server.Databases)
    {
        Console.WriteLine($"database: {db.Name} (compat {db.CompatibilityLevel})");
        var model = db.Model;
        Console.WriteLine($"  tables: {model.Tables.Count}  relationships: {model.Relationships.Count}");

        foreach (var t in model.Tables)
        {
            Console.WriteLine($"  - {t.Name,-18} cols={t.Columns.Count,3} measures={t.Measures.Count,3}");
            foreach (var m in t.Measures)
            {
                Console.WriteLine($"      [{m.Name}]");
            }
        }

        Console.WriteLine("  relationships:");
        foreach (var r in model.Relationships)
        {
            if (r is SingleColumnRelationship scr)
            {
                Console.WriteLine(
                    $"    {scr.FromTable.Name}[{scr.FromColumn.Name}] -> {scr.ToTable.Name}[{scr.ToColumn.Name}]");
            }
            else
            {
                Console.WriteLine($"    {r.FromTable.Name} -> {r.ToTable.Name} ({r.GetType().Name})");
            }
        }
    }
    return 0;
}
catch (Exception ex)
{
    Console.Error.WriteLine($"FAILED: {ex.GetType().Name}: {ex.Message}");
    return 1;
}
