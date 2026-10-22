import re
import unicodedata
from pathlib import Path

import pandas as pd
import folium
from branca.colormap import linear
import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Produção de Café por Município", layout="wide")

ARQUIVO = Path(__file__).parent / "EntidadesSindicais2.xlsx"
URL_MUNICIPIOS = "https://servicodados.ibge.gov.br/api/v1/localidades/estados/31/municipios"
URL_MALHA = (
    "https://servicodados.ibge.gov.br/api/v3/malhas/estados/31"
    "?formato=application/vnd.geo%2Bjson&intrarregiao=municipio&qualidade=minima"
)
URL_MALHA_ESTADO = (
    "https://servicodados.ibge.gov.br/api/v3/malhas/estados/31"
    "?formato=application/vnd.geo%2Bjson&qualidade=minima"
)
COL_QTD = "Quantidade produzida (Ton)"


def normalizar(texto) -> str:
    texto = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", texto)


def achar_coluna(colunas, *termos):
    for col in colunas:
        if all(t in normalizar(col) for t in termos):
            return col
    return None


@st.cache_data
def carregar_ibge():
    municipios = requests.get(URL_MUNICIPIOS, timeout=60).json()
    malha = requests.get(URL_MALHA, timeout=60).json()
    malha_estado = requests.get(URL_MALHA_ESTADO, timeout=60).json()
    tabela = pd.DataFrame(
        {"codigo": str(m["id"]), "Município IBGE": m["nome"], "chave": normalizar(m["nome"])}
        for m in municipios
    )
    return tabela, malha, malha_estado


@st.cache_data
def carregar_dados():
    df = pd.read_excel(ARQUIVO)
    col_mun = achar_coluna(df.columns, "municipio")
    col_qtd = achar_coluna(df.columns, "quantidade", "produzida") or achar_coluna(df.columns, "ton")
    if not col_mun or not col_qtd:
        st.error(f"Colunas não encontradas. Colunas disponíveis: {list(df.columns)}")
        st.stop()

    df = df.rename(columns={col_mun: "Município", col_qtd: COL_QTD})
    df[COL_QTD] = pd.to_numeric(df[COL_QTD].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    df["chave"] = df["Município"].map(normalizar)
    return df.drop_duplicates(subset="chave", keep="first")[["chave", "Município", COL_QTD]]


ibge, malha, malha_estado = carregar_ibge()
dados = carregar_dados()
mapa = ibge.merge(dados[["chave", COL_QTD]], on="chave", how="left")
com_dados = mapa.dropna(subset=[COL_QTD])
nao_encontrados = dados.loc[~dados["chave"].isin(ibge["chave"]), "Município"].tolist()

st.title("Produção de Café por Município – Minas Gerais")

producao = dict(zip(mapa["codigo"], mapa[COL_QTD]))
nomes = dict(zip(mapa["codigo"], mapa["Município IBGE"]))
max_producao = float(mapa[COL_QTD].max()) if not com_dados.empty else 1
escala = linear.YlOrRd_09.scale(0, max(max_producao, 1))
escala.caption = "Quantidade produzida (Ton)"

for feature in malha["features"]:
    codigo = str(feature["properties"]["codarea"])
    valor_producao = producao.get(codigo)
    if pd.isna(valor_producao):
        valor_producao = None
    feature["properties"]["municipio_nome"] = nomes.get(codigo, "")
    feature["properties"]["producao_ton"] = valor_producao
    feature["properties"]["producao_label"] = (
        f"{valor_producao:,.0f}" if valor_producao is not None else "Sem dados"
    )

mapa_folium = folium.Map(location=[-18.5, -44.5], zoom_start=6, tiles=None)
folium.TileLayer(
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    attr="Tiles © Esri",
    name="Dark Gray",
).add_to(mapa_folium)
folium.TileLayer(
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
    attr="Labels © Esri",
    name="Labels",
    overlay=True,
    control=False,
).add_to(mapa_folium)
camada = folium.GeoJson(
    malha,
    style_function=lambda feature: {
        "fillColor": escala(feature["properties"]["producao_ton"])
        if feature["properties"]["producao_ton"] is not None
        else "#22252a",
        "color": "#d4d4d4" if feature["properties"]["producao_ton"] is not None else "#34383d",
        "weight": 0.45 if feature["properties"]["producao_ton"] is not None else 0.25,
        "fillOpacity": 0.8 if feature["properties"]["producao_ton"] is not None else 0,
    },
    tooltip=folium.GeoJsonTooltip(
        fields=["municipio_nome", "producao_label"],
        aliases=["Município:", "Produção (Ton):"],
        localize=True,
        sticky=False,
    ),
).add_to(mapa_folium)
mapa_folium.fit_bounds(camada.get_bounds())
folium.GeoJson(
    malha_estado,
    style_function=lambda _: {
        "fillColor": "#555555",
        "fillOpacity": 0,
        "color": "#555555",
        "weight": 2.5,
        "opacity": 1,
    },
    name="Contorno de Minas Gerais",
    control=False,
    interactive=False,
).add_to(mapa_folium)
escala.add_to(mapa_folium)

components.html(mapa_folium.get_root().render(), height=760, scrolling=False)

if nao_encontrados:
    with st.expander(f"{len(nao_encontrados)} município(s) da planilha não encontrados no IBGE"):
        st.write(nao_encontrados)
