$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

function Invoke-StudyScript {
    param([Parameter(Mandatory = $true)][string]$Script)
    Write-Host "=== $Script"
    python $Script
    if ($LASTEXITCODE -ne 0) {
        throw "Study script failed: $Script"
    }
}

# Downloads skip files that already exist (use --force on the script to refresh).
Invoke-StudyScript .\download_nasa_power_weather.py
Invoke-StudyScript .\download_aemo_prices.py
Invoke-StudyScript .\download_aemo_generation.py
Invoke-StudyScript .\build_cq_topology.py
Invoke-StudyScript .\build_gpu_trace_profile.py

# One-time ANDES code generation, single-process (its parallel mode hangs on Windows).
if (-not (Test-Path "$env:USERPROFILE\.andes\pycode")) {
    python -m andes prepare -q --nomp
    if ($LASTEXITCODE -ne 0) { throw "ANDES code generation failed" }
}

Write-Host "=== unit tests"
python -m pytest
if ($LASTEXITCODE -ne 0) { throw "Unit tests failed" }

Invoke-StudyScript .\run_regional_equivalent_sweep.py
Invoke-StudyScript .\plot_regional_equivalent_results.py
Invoke-StudyScript .\run_pilbara_ring_study.py
Invoke-StudyScript .\run_pilbara_sensitivity.py
Invoke-StudyScript .\run_pilbara_uncertainty.py
Invoke-StudyScript .\run_renewable_bess_assessment.py
Invoke-StudyScript .\run_advanced_resource_study.py
Invoke-StudyScript .\run_multisite_comparison.py
Invoke-StudyScript .\run_solar_sensitivity.py
Invoke-StudyScript .\run_solar_validation.py
Invoke-StudyScript .\run_cost_uncertainty.py
Invoke-StudyScript .\run_climate_scenarios.py
Invoke-StudyScript .\run_backup_reliability.py
Invoke-StudyScript .\run_price_aware_dispatch.py
Invoke-StudyScript .\run_recent_prices.py
Invoke-StudyScript .\run_flexible_load.py
Invoke-StudyScript .\run_trace_load_profile.py
Invoke-StudyScript .\run_growth_scenarios.py
Invoke-StudyScript .\run_oscillation_screening.py
Invoke-StudyScript .\run_cq_multibus_study.py
Invoke-StudyScript .\run_hourly_power_flow.py
Invoke-StudyScript .\run_dynamic_screening.py
Invoke-StudyScript .\run_rms_dynamic_study.py
Invoke-StudyScript .\run_rms_sensitivity.py
Invoke-StudyScript .\run_model_validation.py
Invoke-StudyScript .\generate_study_report.py

Write-Host "Finished. Results are in $here\results (start with AUTOMATED_STUDY_REPORT.md)"
