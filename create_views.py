import sqlite3
import pandas as pd

# 1. Connect to your SQLite database
conn = sqlite3.connect("ific3074.sqlite")
cur = conn.cursor()

# 2. Execute DDL to build the Space IFIC #3074 view
view_statement = """
CREATE VIEW IF NOT EXISTS vw_space_3074_pfd_inputs AS
SELECT 
    n.ntc_id,
    n.adm AS submitting_country,
    n.wic_no AS issue_no,
    n.ntf_rsn,
    n.st_cur,
    g.grp_id,
    g.beam_name,
    g.emi_rcp,
    f.freq_min,
    f.freq_max,
    f.freq_mhz,
    e.pwr_ds_max,
    sb.gain AS peak_antenna_gain,
    geo.long_nom AS gso_longitude,
    t.coord_prov AS affected_country,
    t.agree_st
FROM notice n
JOIN grp g ON n.ntc_id = g.ntc_id
JOIN freq f ON g.grp_id = f.grp_id
LEFT JOIN emiss e ON g.grp_id = e.grp_id
LEFT JOIN s_beam sb ON n.ntc_id = sb.ntc_id AND g.beam_name = sb.beam_name
LEFT JOIN geo ON n.ntc_id = geo.ntc_id
LEFT JOIN tr_aff_ntw t ON n.ntc_id = t.ntc_id AND t.coord_prov = 'FIN'
WHERE n.wic_no = 3074
    AND g.emi_rcp = 'E';
"""

cur.execute(view_statement)
conn.commit()

# 3. Test query against the view
test_query = """
SELECT ntc_id, submitting_country, beam_name, freq_min, freq_max, pwr_ds_max, peak_antenna_gain
FROM vw_space_3074_pfd_inputs
WHERE emi_rcp = 'E'
LIMIT 5;
"""

df = pd.read_sql_query(test_query, conn)
print("View Output Sample:")
print(df)

conn.close()
