import json
import re
import unicodedata
from pathlib import Path

import pandas as pd
import folium
from branca.element import MacroElement, Template
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
    df["Situacao"] = df["Situacao"].fillna("").astype(str).str.strip()
    registros = df[["codigo", "Razao Social", "Situacao"]].copy()
    for coluna in ["Razao Social", "Situacao"]:
        registros[coluna] = registros[coluna].fillna("").astype(str).str.strip()
    registros["chave_sindicato"] = registros["Razao Social"].map(normalizar)
    registros = registros.loc[registros["chave_sindicato"].ne("")].copy()
    juntar = lambda valores: " | ".join(dict.fromkeys(valor for valor in valores if valor))
    registros = registros.groupby(["codigo", "chave_sindicato"], sort=False).agg(
        sindicato=("Razao Social", "first"), situacao=("Situacao", juntar)
    ).reset_index()
    por_municipio = {
        codigo: grupo[["sindicato", "situacao"]].to_dict("records")
        for codigo, grupo in registros.groupby("codigo", sort=False)
    }
    sindicatos = (
        df.groupby("codigo")
        .agg(
            **{"Produz Cafe": ("Produz Cafe", "any"), "Tem Sindicato": ("Tem Sindicato", "any")},
        )
        .reset_index()
    )
    sindicatos["registros_sindicatos"] = sindicatos["codigo"].map(
        lambda codigo: por_municipio.get(codigo, [])
    )
    sindicatos["Sindicato"] = sindicatos["registros_sindicatos"].map(
        lambda registros: juntar(registro["sindicato"] for registro in registros)
    )
    sindicatos["Situacao"] = sindicatos["registros_sindicatos"].map(
        lambda registros: juntar(registro["situacao"] for registro in registros)
    )
    sindicatos["Tem Sindicato"] = sindicatos["Tem Sindicato"] | sindicatos["registros_sindicatos"].map(
        lambda registros: len(registros) > 0
    )
    unico = df.drop_duplicates(subset="codigo", keep="first")[["codigo", "Município", COL_QTD]]
    return unico.merge(sindicatos, on="codigo", how="left")


def adicionar_tabela(mapa_folium, camada):
    tabela = MacroElement()
    tabela.mapa = mapa_folium.get_name()
    tabela.camada = camada.get_name()
    tabela._template = Template("""
        {% macro header(this, kwargs) %}
        <style>
            html, body { background: #0e1117 !important; color-scheme: dark; }
            #{{ this.mapa }} { height: 760px !important; }
            .municipios { margin-top: 32px; padding: 16px 0; font-family: sans-serif;
                color: #f0f2f6; background: #0e1117; color-scheme: dark; }
            .municipios-rodape { display: flex; align-items: center; flex-wrap: wrap;
                gap: 12px; margin-top: 16px; }
            .municipios h2 { font-size: 20px; margin: 0 0 16px; }
            .municipios button { border: 1px solid #5b616c; border-radius: 4px;
                background: #262a33; padding: 6px 12px; color: #f0f2f6; }
            .municipios button:disabled { opacity: .5; }
            .municipios-busca { position: relative; margin-bottom: 16px; font-size: 14px; }
            .municipios-busca input { width: 100%; box-sizing: border-box; padding: 10px 12px 10px 36px;
                border: 1px solid #5b616c; border-radius: 4px; background: #262a33; color: #f0f2f6; }
            .municipios-busca input:focus-visible { outline: 2px solid #f5d76e; }
            .municipios-busca-icone { position: absolute; left: 12px; top: 50%;
                transform: translateY(-50%); color: #a0a6b0; pointer-events: none; }
            .municipios-busca input:not(:placeholder-shown) { padding-left: 12px; }
            .municipios-busca input:not(:placeholder-shown) + .municipios-busca-icone { display: none; }
            .municipios-rolagem { max-height: 320px; overflow: auto; }
            .municipios table { width: 100%; border-collapse: collapse; font-size: 14px; }
            .municipios th { position: sticky; top: 0; background: #262a33; z-index: 1; }
            .municipios th, .municipios td { padding: 10px 12px; border-bottom: 1px solid #363b45;
                text-align: left; vertical-align: top; overflow-wrap: anywhere; min-width: 130px; }
            .municipios td:last-child, .municipios th:last-child { text-align: right; }
            .municipios tbody tr { cursor: pointer; }
            .municipios tbody tr:hover, .municipios tbody tr:focus-visible { background: #303641; }
            .municipios tbody tr:focus-visible { outline: 2px solid #f5d76e; outline-offset: -2px; }
            .municipios tr[aria-selected="true"] { background: #37433f; color: #fff; }
            .municipios tr[aria-selected="true"] td:first-child { box-shadow: inset 3px 0 #f5d76e; }
        </style>
        {% endmacro %}
        {% macro html(this, kwargs) %}
        <section class="municipios" aria-label="Tabela de municípios">
            <h2>Informações dos Sindicatos de Café</h2>
            <div class="municipios-busca">
                <input id="{{ this.get_name() }}_busca" type="search" autocomplete="off"
                    aria-label="Buscar município" placeholder=" ">
                <span class="municipios-busca-icone" aria-hidden="true"><i class="fa fa-search"></i></span>
            </div>
            <div class="municipios-rolagem">
                <table aria-label="Dados dos municípios">
                    <thead><tr><th scope="col">Município</th><th scope="col">Sindicato</th>
                        <th scope="col">Situação do Cadastro</th><th scope="col">Produção (Ton)</th></tr></thead>
                    <tbody id="{{ this.get_name() }}_linhas"></tbody>
                </table>
            </div>
            <div class="municipios-rodape">
                <span id="{{ this.get_name() }}_contagem" role="status"></span>
                <button id="{{ this.get_name() }}_limpar" type="button" disabled>Limpar seleção</button>
            </div>
        </section>
        {% endmacro %}
        {% macro script(this, kwargs) %}
        (() => {
            const mapa = {{ this.mapa }};
            const camada = {{ this.camada }};
            const municipios = camada.getLayers().sort((primeiro, segundo) =>
                primeiro.feature.properties.municipio_nome.localeCompare(
                    segundo.feature.properties.municipio_nome, 'pt-BR'));
            const tooltip = camada.getTooltip();
            camada.unbindTooltip();
            municipios.forEach(municipio => municipio.bindTooltip(
                tooltip.getContent(), {...tooltip.options}));
            const linhas = document.getElementById('{{ this.get_name() }}_linhas');
            const contagem = document.getElementById('{{ this.get_name() }}_contagem');
            const limpar = document.getElementById('{{ this.get_name() }}_limpar');
            const busca = document.getElementById('{{ this.get_name() }}_busca');
            const selecionados = new Set();
            const estilos = new Map(municipios.map(municipio => [municipio, {...municipio.options}]));
            const registros = [];
            const normalizarBusca = texto => texto.normalize('NFD').replace(/[\u0300-\u036f]/g, '')
                .toLocaleLowerCase('pt-BR').trim();

            function atualizar() {
                municipios.forEach(municipio => {
                    const ativo = selecionados.has(String(municipio.feature.properties.codarea));
                    const estilo = estilos.get(municipio);
                    municipio.setStyle(ativo ? {
                        fillColor: estilo.fillColor, fillOpacity: 0.9,
                        color: '#f5d76e', weight: 2.5, opacity: 1
                    } : {
                        fillColor: estilo.fillColor, color: estilo.color, weight: estilo.weight,
                        fillOpacity: selecionados.size ? estilo.fillOpacity * 0.2 : estilo.fillOpacity,
                        opacity: selecionados.size ? 0.25 : 1
                    });
                    if (ativo) {
                        municipio.bringToFront();
                    } else {
                        municipio.closeTooltip();
                    }
                });
                const termo = normalizarBusca(busca.value);
                const filtrados = registros.filter(registro =>
                    (!selecionados.size || selecionados.has(registro.codigo)) && registro.nome.includes(termo));
                linhas.replaceChildren();
                filtrados.forEach(registro => {
                    registro.linha.setAttribute('aria-selected', String(selecionados.has(registro.codigo)));
                    linhas.appendChild(registro.linha);
                });
                if (!filtrados.length) {
                    const celula = linhas.insertRow().insertCell();
                    celula.colSpan = 4;
                    celula.textContent = 'Nenhum município encontrado.';
                }
                const exibidos = new Set(filtrados.map(registro => registro.codigo));
                contagem.textContent = selecionados.size + ' município(s) selecionado(s) de ' + municipios.length
                    + ' ';
                limpar.disabled = selecionados.size === 0 && !busca.value;
            }

            function limparSelecao() {
                selecionados.clear();
                atualizar();
            }

            function selecionar(municipio, evento = {}, origemMapa = false) {
                const codigo = String(municipio.feature.properties.codarea);
                if (evento.ctrlKey || evento.metaKey) {
                    if (selecionados.has(codigo)) selecionados.delete(codigo);
                    else selecionados.add(codigo);
                } else if ((origemMapa && selecionados.size) || selecionados.has(codigo)) {
                    limparSelecao();
                    return;
                } else {
                    selecionados.clear();
                    selecionados.add(codigo);
                }
                atualizar();
                if (selecionados.has(codigo)) municipio.openTooltip(municipio.getBounds().getCenter());
                if (origemMapa) linhas.closest('.municipios-rolagem').scrollTop = 0;
            }

            municipios.forEach(municipio => {
                const propriedades = municipio.feature.properties;
                const sindicatos = propriedades.registros_sindicatos.length
                    ? propriedades.registros_sindicatos : [{sindicato: '-', situacao: '-'}];
                sindicatos.forEach(sindicato => {
                    const linha = document.createElement('tr');
                    linha.tabIndex = 0;
                    linha.dataset.codigo = String(propriedades.codarea);
                    [propriedades.municipio_nome, sindicato.sindicato, sindicato.situacao || '-',
                        propriedades.producao_label].forEach(valor => {
                        const celula = document.createElement('td');
                        celula.textContent = valor;
                        linha.appendChild(celula);
                    });
                    linha.addEventListener('click', evento => selecionar(municipio, evento));
                    linha.addEventListener('keydown', evento => {
                        if (evento.key === 'Enter' || evento.key === ' ') {
                            evento.preventDefault();
                            selecionar(municipio, evento);
                        }
                    });
                    registros.push({codigo: String(propriedades.codarea),
                        nome: normalizarBusca(propriedades.municipio_nome), linha});
                    linhas.appendChild(linha);
                });
                municipio.options.bubblingMouseEvents = false;
                municipio.on('click', evento => selecionar(municipio, evento.originalEvent, true));
            });
            mapa.on('click', limparSelecao);
            busca.addEventListener('input', () => {
                atualizar();
                linhas.closest('.municipios-rolagem').scrollTop = 0;
            });
            limpar.addEventListener('click', () => {
                busca.value = '';
                limparSelecao();
            });
            atualizar();
        })();
        {% endmacro %}
    """)
    tabela.add_to(mapa_folium)


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
    dados[["codigo", COL_QTD, "Sindicato", "Situacao", "Produz Cafe", "Tem Sindicato", "registros_sindicatos"]],
    on="codigo", how="left",
)
com_dados = mapa.dropna(subset=[COL_QTD])
nao_encontrados = dados.loc[~dados["codigo"].isin(ibge["codigo"]), "Município"].tolist()

producao = dict(zip(mapa["codigo"], mapa[COL_QTD]))
nomes = dict(zip(mapa["codigo"], mapa["Município IBGE"]))
sindicatos = dict(zip(mapa["codigo"], mapa["Sindicato"]))
situacoes = dict(zip(mapa["codigo"], mapa["Situacao"]))
registros_sindicatos = dict(zip(mapa["codigo"], mapa["registros_sindicatos"]))
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
    registros = registros_sindicatos.get(codigo)
    feature["properties"]["registros_sindicatos"] = registros if isinstance(registros, list) else []
    feature["properties"]["producao_ton"] = valor_producao
    possui_sindicato = tem_sindicato.get(codigo, False)
    visivel = (
        filtro_sindicato == "Ambos"
        or (filtro_sindicato == "Com sindicato" and possui_sindicato)
        or (filtro_sindicato == "Sem sindicato" and not possui_sindicato)
    )
    feature["properties"]["visivel"] = visivel
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
    {"type": "FeatureCollection", "features": [
        feature for feature in malha["features"] if feature["properties"]["visivel"]
    ]},
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
mapa_folium.fit_bounds(folium.GeoJson(malha_estado).get_bounds())
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

adicionar_tabela(mapa_folium, camada)
st.subheader("Produção de Café em Toneladas")
components.html(mapa_folium.get_root().render(), height=1400, scrolling=False)

if nao_encontrados:
    with st.expander(f"{len(nao_encontrados)} município(s) da planilha não encontrados no IBGE"):
        st.write(nao_encontrados)
