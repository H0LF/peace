# The installer's model choice, without installing anything: loads only its functions, then checks them.
$ErrorActionPreference = 'Stop'
$ast = [System.Management.Automation.Language.Parser]::ParseFile("$PSScriptRoot/../install.ps1", [ref]$null, [ref]$null)
foreach ($name in 'Get-ModelChoice', 'Get-RecommendedChoice', 'Read-Choice') {
    . ([scriptblock]::Create($ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true).Extent.Text))
}
function Assert-Recommended($vram, $ram, $free, $expected) {
    $got = (Get-RecommendedChoice (Get-ModelChoice $vram $ram $free)).Model
    if ($got -ne $expected) { throw "VRAM $vram, RAM $ram, disk $free`: expected '$expected', got '$got'" }
}
Assert-Recommended 16 32 500 'gpt-oss:20b'    # the big one only with the memory for it
Assert-Recommended 8 16 500 'qwen3:8b'
Assert-Recommended 0 32 500 'qwen3:8b'        # no graphics card, plenty of RAM
Assert-Recommended 2 16 500 'qwen3:4b'        # typical laptop: never the 13 GB model
Assert-Recommended 0 8 500 'llama3.2:3b'
Assert-Recommended 0 4 500 ''                 # too little memory for any model
Assert-Recommended 16 32 25 'qwen3:8b'        # powerful PC, no disk space for gpt-oss
Assert-Recommended 16 32 16 ''                # no room for any model

$tooFull = @(Get-ModelChoice 16 32 16)
if ($tooFull[0].Room) { throw 'a model without disk space must not be offered' }
if (-not $tooFull[-1].Room) { throw '"no model" needs no disk space' }
if (@(Get-ModelChoice 0 8 500)[0].Fits) { throw 'gpt-oss must be marked as slow on this PC' }
if ((Read-Choice @('1', '2') 2 0) -ne 2) { throw 'no answer must take the recommendation' }
Write-Host 'install_choice: ok'
