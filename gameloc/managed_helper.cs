// Gameloc helper: inspect and rewrite managed ldstr operands with Mono.Cecil.
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using Mono.Cecil;
using Mono.Cecil.Cil;

static class GamelocManagedHelper
{
    static IEnumerable<TypeDefinition> Types(IEnumerable<TypeDefinition> roots)
    {
        foreach (var type in roots) {
            yield return type;
            foreach (var child in Types(type.NestedTypes)) yield return child;
        }
    }

    static string B64(string value)
    {
        return Convert.ToBase64String(Encoding.UTF8.GetBytes(value ?? ""));
    }

    static string Key(MethodDefinition method, Instruction instruction)
    {
        return method.MetadataToken.ToUInt32().ToString("X8") + ":" +
               instruction.Offset.ToString("X8");
    }

    static int List(string path)
    {
        var resolver = new DefaultAssemblyResolver();
        resolver.AddSearchDirectory(Path.GetDirectoryName(Path.GetFullPath(path)));
        using (var module = ModuleDefinition.ReadModule(path,
                   new ReaderParameters { ReadSymbols = false, InMemory = true,
                                          AssemblyResolver = resolver })) {
            foreach (var type in Types(module.Types)) {
                foreach (var method in type.Methods) {
                    if (!method.HasBody) continue;
                    foreach (var ins in method.Body.Instructions) {
                        if (ins.OpCode != OpCodes.Ldstr || !(ins.Operand is string)) continue;
                        Console.WriteLine(Key(method, ins) + "\t" +
                            B64((string)ins.Operand) + "\t" + B64(type.FullName) + "\t" +
                            B64(method.Name));
                    }
                }
            }
        }
        return 0;
    }

    static int Patch(string input, string mapPath, string output)
    {
        var edits = new Dictionary<string, string>();
        foreach (var line in File.ReadAllLines(mapPath, Encoding.UTF8)) {
            var parts = line.Split(new [] {'\t'}, 2);
            if (parts.Length == 2)
                edits[parts[0]] = Encoding.UTF8.GetString(Convert.FromBase64String(parts[1]));
        }
        int changed = 0;
        var resolver = new DefaultAssemblyResolver();
        resolver.AddSearchDirectory(Path.GetDirectoryName(Path.GetFullPath(input)));
        using (var module = ModuleDefinition.ReadModule(input,
                   new ReaderParameters { ReadSymbols = false, InMemory = true,
                                          AssemblyResolver = resolver })) {
            foreach (var type in Types(module.Types)) {
                foreach (var method in type.Methods) {
                    if (!method.HasBody) continue;
                    foreach (var ins in method.Body.Instructions) {
                        string value;
                        if (ins.OpCode == OpCodes.Ldstr && edits.TryGetValue(Key(method, ins), out value)) {
                            ins.Operand = value;
                            changed++;
                        }
                    }
                }
            }
            module.Write(output, new WriterParameters { WriteSymbols = false });
        }
        Console.WriteLine(changed.ToString());
        return changed == edits.Count ? 0 : 3;
    }

    public static int Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        try {
            if (args.Length == 2 && args[0] == "list") return List(args[1]);
            if (args.Length == 4 && args[0] == "patch") return Patch(args[1], args[2], args[3]);
            Console.Error.WriteLine("list <assembly> | patch <assembly> <map> <output>");
            return 2;
        } catch (Exception ex) {
            Console.Error.WriteLine(ex.ToString());
            return 1;
        }
    }
}
