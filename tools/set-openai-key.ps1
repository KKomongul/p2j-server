Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$envFile = Join-Path (Split-Path $PSScriptRoot -Parent) '.env'
$form = New-Object System.Windows.Forms.Form
$form.Text = 'P2J - OpenAI API key (local only)'
$form.Size = New-Object System.Drawing.Size(580,240)
$form.StartPosition = 'CenterScreen'
$form.TopMost = $true
$label = New-Object System.Windows.Forms.Label
$label.Text = "OpenAI API key를 입력하세요. 입력은 가려집니다.`n서버 .env에만 저장됩니다 (Git 제외 / 로그 출력 없음)."
$label.SetBounds(20,20,530,55)
$box = New-Object System.Windows.Forms.TextBox
$box.UseSystemPasswordChar = $true
$box.SetBounds(20,80,520,28)
$save = New-Object System.Windows.Forms.Button
$save.Text = '저장'
$save.SetBounds(350,135,90,35)
$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = '취소'
$cancel.SetBounds(450,135,90,35)
$cancel.Add_Click({ $form.Close() })
$save.Add_Click({
    $value = $box.Text.Trim()
    if ($value -notmatch '^sk-[A-Za-z0-9_-]+$') {
        [void][System.Windows.Forms.MessageBox]::Show('OpenAI API 키 형식을 확인해 주세요. 키 값은 출력하지 않습니다.')
        return
    }
    try {
        $content = [System.IO.File]::ReadAllText($envFile)
        $line = 'OPENAI_API_KEY=' + $value
        if ($content -match '(?m)^OPENAI_API_KEY=') {
            $content = [regex]::Replace($content, '(?m)^OPENAI_API_KEY=.*$', $line)
        } else { $content += "`r`n$line`r`n" }
        [System.IO.File]::WriteAllText($envFile, $content, (New-Object System.Text.UTF8Encoding($false)))
        $box.Clear()
        $value = $null
        $content = $null
        [void][System.Windows.Forms.MessageBox]::Show('저장했습니다. Codex가 서버 반영 및 연결을 확인합니다.')
        $form.Close()
    } catch {
        [void][System.Windows.Forms.MessageBox]::Show('저장하지 못했습니다. 파일 권한을 확인해 주세요.')
    }
})
$form.Controls.AddRange(@($label,$box,$save,$cancel))
$form.AcceptButton = $save
$form.CancelButton = $cancel
[void]$form.ShowDialog()
$form.Dispose()
