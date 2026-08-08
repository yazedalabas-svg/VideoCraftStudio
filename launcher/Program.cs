using System.Diagnostics;
using System.Text;

namespace VideoCraftStudioLauncher;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        var baseDirectory = AppContext.BaseDirectory;
        var appScript = Path.Combine(baseDirectory, "app.py");
        if (!File.Exists(appScript))
        {
            ShowError("لم يتم العثور على app.py بجانب ملف التشغيل.");
            return;
        }

        var python = FindPythonWindowed();
        if (python is null)
        {
            ShowError("لم يتم العثور على Python. شغّل Setup.bat مرة واحدة ثم أعد المحاولة.");
            return;
        }

        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = python,
                Arguments = $"\"{appScript}\"",
                WorkingDirectory = baseDirectory,
                UseShellExecute = false,
                CreateNoWindow = true,
            });
        }
        catch (Exception exception)
        {
            ShowError($"تعذّر تشغيل الاستديو.\n\n{exception.Message}");
        }
    }

    private static string? FindPythonWindowed()
    {
        var configuredPath = Path.Combine(AppContext.BaseDirectory, "python_path.txt");
        if (File.Exists(configuredPath))
        {
            var configured = File.ReadAllText(configuredPath, Encoding.UTF8).Trim();
            if (File.Exists(configured)) return configured;
        }

        var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        var windowsApps = Path.Combine(localAppData, "Microsoft", "WindowsApps");
        if (Directory.Exists(windowsApps))
        {
            try
            {
                var packagedPython = Directory
                    .EnumerateFiles(windowsApps, "pythonw.exe", SearchOption.AllDirectories)
                    .FirstOrDefault(path => path.Contains("PythonSoftwareFoundation", StringComparison.OrdinalIgnoreCase));
                if (packagedPython is not null) return packagedPython;
            }
            catch (UnauthorizedAccessException)
            {
                // Continue with PATH discovery below.
            }
        }

        var pathEntries = (Environment.GetEnvironmentVariable("PATH") ?? "")
            .Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        foreach (var entry in pathEntries)
        {
            var candidate = Path.Combine(entry.Trim('"'), "pythonw.exe");
            if (File.Exists(candidate)) return candidate;
        }
        return null;
    }

    private static void ShowError(string message)
    {
        System.Windows.Forms.MessageBox.Show(
            message,
            "VideoCraft Studio",
            System.Windows.Forms.MessageBoxButtons.OK,
            System.Windows.Forms.MessageBoxIcon.Error
        );
    }
}
