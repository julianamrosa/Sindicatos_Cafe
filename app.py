import json
import re
import unicodedata
from pathlib import Path

import pandas as pd
import folium
from branca.colormap import linear
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Municípios Produtores de Café - Minas Gerais - 2025", layout="wide")

ARQUIVO = Path(__file__).parent / "EntidadesSindicais2.xlsx"
PASTA_DADOS_IBGE = Path(__file__).parent / "dados_ibge"
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
    with (PASTA_DADOS_IBGE / "municipios.json").open(encoding="utf-8") as arquivo:
        municipios = json.load(arquivo)
    with (PASTA_DADOS_IBGE / "malha_municipios.geojson").open(encoding="utf-8") as arquivo:
        malha = json.load(arquivo)
    with (PASTA_DADOS_IBGE / "malha_mg.geojson").open(encoding="utf-8") as arquivo:
        malha_estado = json.load(arquivo)

    tabela = pd.DataFrame(
        {"codigo": str(m["id"]), "Município IBGE": m["nome"], "chave": normalizar(m["nome"])}
        for m in municipios
    )
    return tabela, malha, malha_estado


@st.cache_data
def carregar_dados():
    df = pd.read_excel(ARQUIVO, sheet_name="Nova base")
    colunas = {
        "IBGE": "codigo",
        "Municipio": "Município",
        "Produz_Cafe": "Produz Cafe",
        "Producao_Ton": COL_QTD,
        "Tem_Sindicato": "Tem Sindicato",
        "Razao_Social": "Razao Social",
        "Situacao_do_Cadastro": "Situacao",
    }
    faltantes = set(colunas) - set(df.columns)
    if faltantes:
        st.error(f"Colunas não encontradas na aba 'Nova base': {', '.join(sorted(faltantes))}")
        st.stop()

    df = df.rename(columns=colunas)
    df["codigo"] = pd.to_numeric(df["codigo"], errors="coerce").astype("Int64").astype("string")
    df[COL_QTD] = pd.to_numeric(df[COL_QTD].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    for coluna in ["Produz Cafe", "Tem Sindicato"]:
        df[coluna] = df[coluna].map(
            lambda valor: False if pd.isna(valor) else valor == 1 or normalizar(valor) in {"sim", "s", "true", "yes"}
        )
    df["Razao Social"] = df["Razao Social"].fillna("").astype(str).str.strip()
    # Produção é a mesma nas repetições (não soma); apenas os nomes são reunidos.
    df["Situacao"] = df["Situacao"].fillna("").astype(str).str.strip()
    juntar = lambda s: " e ".join(dict.fromkeys(n for n in s if n))
    sindicatos = (
        df.groupby("codigo")
        .agg(
            Sindicato=("Razao Social", juntar),
            Situacao=("Situacao", juntar),
            **{"Produz Cafe": ("Produz Cafe", "any"), "Tem Sindicato": ("Tem Sindicato", "any")},
        )
        .reset_index()
    )
    unico = df.drop_duplicates(subset="codigo", keep="first")[["codigo", "Município", COL_QTD]]
    return unico.merge(sindicatos, on="codigo", how="left")


st.title("Municípios Produtores de Café - Minas Gerais - 2025")

try:
    with st.spinner("Carregando dados geográficos locais..."):
        ibge, malha, malha_estado = carregar_ibge()
except (OSError, ValueError) as erro:
    detalhes_erro = str(erro)
    st.error(
        "Não foi possível carregar os arquivos geográficos locais. "
        "Verifique se os três arquivos estão na pasta dados_ibge."
    )
    st.caption(f"Detalhes técnicos: {detalhes_erro}")
    if st.button("Tentar novamente"):
        st.rerun()
    st.stop()

st.markdown(
    "<style>"
    '.st-key-filtro_sindicato [role="radiogroup"] { justify-content: flex-end; }'
    '.st-key-filtro_sindicato [role="radiogroup"] > label:last-child { margin-right: 0; }'
    '.st-key-filtro_sindicato [data-testid="stWidgetLabel"] { justify-content: flex-end; text-align: right; }'
    "</style>",
    unsafe_allow_html=True,
)
with st.container(key="filtro_sindicato"):
    filtro_sindicato = st.radio(
        "",
        ["Com sindicato", "Sem sindicato", "Ambos"],
        index=2,
        horizontal=True,
        width="stretch",
    )
try:
    dados = carregar_dados()
except (OSError, ValueError) as erro:
    st.error("Não foi possível ler a aba 'Nova base' de EntidadesSindicais2.xlsx. Verifique o arquivo e feche-o no Excel se estiver aberto.")
    st.caption(f"Detalhes técnicos: {erro}")
    st.stop()
mapa = ibge.merge(
    dados[["codigo", COL_QTD, "Sindicato", "Situacao", "Produz Cafe", "Tem Sindicato"]],
    on="codigo", how="left",
)
com_dados = mapa.dropna(subset=[COL_QTD])
nao_encontrados = dados.loc[~dados["codigo"].isin(ibge["codigo"]), "Município"].tolist()

producao = dict(zip(mapa["codigo"], mapa[COL_QTD]))
nomes = dict(zip(mapa["codigo"], mapa["Município IBGE"]))
sindicatos = dict(zip(mapa["codigo"], mapa["Sindicato"]))
situacoes = dict(zip(mapa["codigo"], mapa["Situacao"]))
produz_cafe = dict(zip(mapa["codigo"], mapa["Produz Cafe"].eq(True)))
tem_sindicato = dict(zip(mapa["codigo"], mapa["Tem Sindicato"].eq(True)))
max_producao = float(mapa[COL_QTD].max()) if not com_dados.empty else 1
escala = linear.YlOrRd_09.scale(0, max(max_producao, 1))
escala.caption = "Com sindicato - Produção (Ton)"
escala_azul = linear.Blues_09.scale(0, max(max_producao, 1))
escala_azul.caption = "Sem sindicato - Produção (Ton)"
escala_azul.position = "bottomleft"

for feature in malha["features"]:
    codigo = str(feature["properties"]["codarea"])
    valor_producao = producao.get(codigo)
    if pd.isna(valor_producao):
        valor_producao = None
    feature["properties"]["municipio_nome"] = nomes.get(codigo, "")
    feature["properties"]["producao_ton"] = valor_producao
    possui_sindicato = tem_sindicato.get(codigo, False)
    visivel = (
        filtro_sindicato == "Ambos"
        or (filtro_sindicato == "Com sindicato" and possui_sindicato)
        or (filtro_sindicato == "Sem sindicato" and not possui_sindicato)
    )
    colorido = produz_cafe.get(codigo, False) and valor_producao is not None and valor_producao > 0 and visivel
    feature["properties"]["colorido"] = colorido
    feature["properties"]["cor"] = (
        (escala if possui_sindicato else escala_azul)(valor_producao)
        if colorido else "#22252a"
    )
    nome_sindicato = sindicatos.get(codigo)
    feature["properties"]["sindicato"] = nome_sindicato if isinstance(nome_sindicato, str) and nome_sindicato else "-"
    situacao = situacoes.get(codigo)
    feature["properties"]["situacao"] = situacao if isinstance(situacao, str) and situacao else "-"
    feature["properties"]["producao_label"] = (
        f"{valor_producao:,.0f}" if valor_producao is not None else "-"
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
        "fillColor": feature["properties"]["cor"],
        "color": "#d4d4d4" if feature["properties"]["colorido"] else "#34383d",
        "weight": 0.45 if feature["properties"]["colorido"] else 0.25,
        "fillOpacity": 0.8 if feature["properties"]["colorido"] else 0,
    },
    tooltip=folium.GeoJsonTooltip(
        fields=["municipio_nome", "sindicato", "situacao", "producao_label"],
        aliases=["Município:", "Sindicato:", "Situação do Cadastro:", "Produção (Ton):"],
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
if filtro_sindicato != "Sem sindicato":
    escala.add_to(mapa_folium)
if filtro_sindicato != "Com sindicato":
    escala_azul.add_to(mapa_folium)
folium.Element(
    "<style>.legend text { fill: #ffffff !important; }"
    ".leaflet-tooltip { white-space: normal !important; width: max-content; max-width: 380px; }"
    ".leaflet-tooltip table { white-space: normal !important; }</style>"
).add_to(mapa_folium.get_root().header)

components.html(mapa_folium.get_root().render(), height=760, scrolling=False)

if nao_encontrados:
    with st.expander(f"{len(nao_encontrados)} município(s) da planilha não encontrados no IBGE"):
        st.write(nao_encontrados)
