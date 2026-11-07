import pandas as pd

df_pfd = pd.read_json("pfd.json")
df_m4 = pd.read_json("m4.json")
df_traficom = pd.read_excel("Traficom_SinceAI_Frequencies_of_interest_v05112026.xlsx",header=11)

df_m42 = pd.json_normalize(df_m4['value'], max_level=0)
print(df_m42.head(2))