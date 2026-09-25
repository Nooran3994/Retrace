$file = 'C:\Users\HP\OneDrive\Desktop\SYSTEMS\SCAAI_SYSTEMS\Retrace\scripts\install.ps1'
$content = Get-Content -Raw $file
$errors = $null
$null = [System.Management.Automation.Language.Parser]::ParseScript($content, $null, $errors)
if ($errors -and $errors.Count -gt 0) {
    Write-Host "PARSE FAIL: $($errors.Count) error(s)"
    foreach ($e in $errors) { Write-Host "  line $($e.Number): $($e.Message)" }
} else {
    Write-Host 'PARSE OK'
}