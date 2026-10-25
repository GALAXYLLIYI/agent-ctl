# AB 靶场（WinForms 版）——UIA 有名字的 8 控件表单
# 为什么另起一个：tkinter 对 UIA 只暴露无名 Pane，按名字定位不可用（2026-10-25 实测）。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File tools\ab_target_winforms.ps1 -Out <json>
param([string]$Out = "$env:TEMP\ab_target_winforms.json")

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = New-Object System.Windows.Forms.Form
$form.Text = "AB靶场 WinForms｜8 控件"
$form.Size = New-Object System.Drawing.Size(520, 460)
$form.StartPosition = "CenterScreen"

$y = 14
function Add-Row([string]$label, [System.Windows.Forms.Control]$ctrl) {
    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Text = $label
    $lbl.Location = New-Object System.Drawing.Point(14, ($script:y + 4))
    $lbl.Size = New-Object System.Drawing.Size(130, 22)
    $form.Controls.Add($lbl)
    $ctrl.Location = New-Object System.Drawing.Point(150, $script:y)
    $ctrl.Size = New-Object System.Drawing.Size(330, 24)
    $form.Controls.Add($ctrl)
    $script:y += 36
}

$texts = @{}
foreach ($k in @("text_1", "text_2", "text_3")) {
    $tb = New-Object System.Windows.Forms.TextBox
    $tb.Name = $k
    $texts[$k] = $tb
    Add-Row $k $tb
}

$combos = @{}
$i = 1
foreach ($k in @("combo_1", "combo_2")) {
    $cb = New-Object System.Windows.Forms.ComboBox
    $cb.Name = $k
    $cb.DropDownStyle = "DropDownList"
    [void]$cb.Items.Add("选项A"); [void]$cb.Items.Add("选项B"); [void]$cb.Items.Add("选项C")
    $cb.SelectedIndex = 0
    $combos[$k] = $cb
    Add-Row ("$k（档$i）") $cb
    $i++
}

$checks = @{}
foreach ($k in @("check_1", "check_2")) {
    $chk = New-Object System.Windows.Forms.CheckBox
    $chk.Name = $k
    $chk.Text = "复选 $k"
    $checks[$k] = $chk
    Add-Row "" $chk
}

$btn = New-Object System.Windows.Forms.Button
$btn.Name = "submit"
$btn.Text = "完成"
$btn.Location = New-Object System.Drawing.Point(150, $script:y)
$btn.Size = New-Object System.Drawing.Size(330, 32)
$form.Controls.Add($btn)

$btn.Add_Click({
    $payload = [ordered]@{}
    foreach ($k in $texts.Keys) { $payload[$k] = $texts[$k].Text }
    foreach ($k in $combos.Keys) { $payload[$k] = $combos[$k].SelectedItem }
    foreach ($k in $checks.Keys) { $payload[$k] = $checks[$k].Checked }
    $payload["submit"] = "完成"
    ($payload | ConvertTo-Json -Compress) | Set-Content -Path $Out -Encoding UTF8
    $form.Close()
})

[void]$form.ShowDialog()
