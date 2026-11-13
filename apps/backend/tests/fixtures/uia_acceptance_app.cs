using System;
using System.IO;
using System.Windows.Forms;

// A deliberately ordinary WinForms program: a window, a text box, a button, a status label.
// It is the "third-party application" the UI Automation acceptance drives. Nothing in it knows
// about Canalla; it only reads and writes the file path it is given.
internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        string path = args.Length > 0 ? args[0] : Path.Combine(Path.GetTempPath(), "uia_out.txt");
        var form = new Form
        {
            Text = "Canalla Acceptance App",
            Width = 620,
            Height = 240,
            StartPosition = FormStartPosition.CenterScreen
        };
        var box = new TextBox
        {
            Name = "InputBox",
            AccessibleName = "InputBox",
            Left = 20,
            Top = 20,
            Width = 540
        };
        if (File.Exists(path))
        {
            box.Text = File.ReadAllText(path);
            // Independent proof that the second launch really reloaded the saved document.
            File.WriteAllText(path + ".loaded", box.Text);
        }
        var save = new Button
        {
            Name = "SaveButton",
            AccessibleName = "Save",
            Text = "Save",
            Left = 20,
            Top = 70,
            Width = 120
        };
        var status = new Label
        {
            Name = "StatusLabel",
            AccessibleName = "Status",
            Text = "idle",
            Left = 170,
            Top = 78,
            AutoSize = true
        };
        save.Click += delegate
        {
            File.WriteAllText(path, box.Text);
            status.Text = "saved";
        };
        form.Controls.Add(box);
        form.Controls.Add(save);
        form.Controls.Add(status);
        Application.Run(form);
    }
}
