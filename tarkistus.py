import sqlite3
import pandas as pd

# Varmista, että tietokannan nimi vastaa omaasi
DB_NAME = "ific3074.sqlite"

with sqlite3.connect(DB_NAME) as conn:
    # Etsitään koodisi luoma näkymä (view). Jos se on nimetty eri tavalla koodissasi, vaihda se tähän.
    query = """
        SELECT ntc_id, freq_min, freq_max, pwr_ds_max, peak_antenna_gain, emi_rcp
        FROM vw_space_3074_pfd_inputs
        WHERE pwr_ds_max IS NOT NULL
        ORDER BY peak_antenna_gain
        LIMIT 5;
    """
    try:
        df = pd.read_sql_query(query, conn)
        print("Tässä 5 ensimmäistä riviä suoraan SQL-tietokannasta:")
        print(df)
    except Exception as e:
        print("Virhe kyselyssä (näkymän nimi voi olla eri):", e)
        
        # Jos view ei toimi, katsotaan mitä tauluja tietokannassa ylipäätään on:
        tables = pd.read_sql_query("SELECT name FROM sqlite_master WHERE type='table' OR type='view';", conn)
        print("\nTietokannan sisältämät taulut ja näkymät:")
        print(tables['name'].tolist())