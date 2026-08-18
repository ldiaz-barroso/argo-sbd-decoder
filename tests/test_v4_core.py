from pathlib import Path
import sys
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_scientific_products import add_navigation_qc, add_profile_qc, QC_BAD, QC_PROBABLY_BAD
from generate_quicklook_products import classify_fix_from_cycle_timing

CFG = {
    'pressure_min': -5.0, 'pressure_max': 12000.0,
    'temperature_min': -2.5, 'temperature_max': 45.0,
    'salinity_min': 0.0, 'salinity_max': 45.0,
    'min_profile_levels': 3, 'pressure_reversal_tolerance': 5.0,
    'max_temp_step': 5.0, 'max_sal_step': 3.0,
    'max_surface_speed_ms': 3.0,
}

def test_profile_qc_flags_out_of_range():
    df = pd.DataFrame({
        'PROFILE_ID':['1']*3, 'PRES':[0,10,20], 'TEMP':[20,60,18],
        'PSAL':[35,35,35], 'SOURCE_FILE':['raw.csv']*3,
    })
    out = add_profile_qc(df, CFG)
    assert int(out.loc[1, 'TEMP_QC']) >= QC_PROBABLY_BAD

def test_navigation_qc_flags_non_increasing_time():
    df = pd.DataFrame({
        'TIME':pd.to_datetime(['2026-01-01','2026-01-01']),
        'LATITUDE':[39,39], 'LONGITUDE':[3,3.1], 'HAS_PROFILE':[True,False]
    })
    out = add_navigation_qc(df, CFG)
    assert int(out.loc[1, 'POSITION_QC']) == QC_BAD

def test_cycle_start_zero_is_not_automatically_gps_only():
    # Minimal NKE-like frame where timing values include a valid zero.
    df = pd.DataFrame({0:['CYCLE TIMING','Cycle start time','Descent start time','Drift start time','Ascent start time','Ascent end time'],
                       1:['',0,1,2,3,4]})
    record_type, has_profile, _ = classify_fix_from_cycle_timing(df, 0)
    assert has_profile is True
    assert record_type.lower() == 'profile'
