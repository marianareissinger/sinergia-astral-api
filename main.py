from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
from datetime import datetime
from typing import Optional
import os

app = FastAPI(
    title="Sinergia Astral — Panchanga API",
    description="Fornece os dados diários do Panchanga védico para o GPT Sinergia Astral.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PROKERALA_CLIENT_ID = os.environ["PROKERALA_CLIENT_ID"]
PROKERALA_CLIENT_SECRET = os.environ["PROKERALA_CLIENT_SECRET"]

PROKERALA_TOKEN_URL = "https://api.prokerala.com/token"
# Endpoint correto conforme spec oficial (sem o 'a' final)
PROKERALA_PANCHANG_URL = "https://api.prokerala.com/v2/astrology/panchang/advanced"


async def obter_token() -> str:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            PROKERALA_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": PROKERALA_CLIENT_ID,
                "client_secret": PROKERALA_CLIENT_SECRET,
            },
        )
        r.raise_for_status()
        return r.json()["access_token"]


def primeiro(lista, campo, padrao=""):
    """Pega o campo do primeiro item de uma lista retornada pela Prokerala."""
    if isinstance(lista, list) and lista:
        return lista[0].get(campo, padrao)
    return padrao


def primeiro_aninhado(lista, *chaves, padrao=""):
    """Navega em chaves aninhadas dentro do primeiro item de uma lista."""
    if not isinstance(lista, list) or not lista:
        return padrao
    obj = lista[0]
    for chave in chaves:
        if not isinstance(obj, dict):
            return padrao
        obj = obj.get(chave, padrao)
    return obj if obj not in ({}, None) else padrao


@app.get(
    "/panchanga",
    summary="Dados do Panchanga para uma data e localização",
    response_description="Tithi, Nakshatra, Yoga, Karana, Vara e períodos do dia.",
)
async def panchanga(
    data: Optional[str] = Query(
        None,
        description="Data no formato YYYY-MM-DD. Sem valor: usa o dia de hoje.",
    ),
    latitude: float = Query(-23.5505, description="Latitude. Padrão: São Paulo."),
    longitude: float = Query(-46.6333, description="Longitude. Padrão: São Paulo."),
):
    """
    Retorna os cinco elementos do Panchanga védico:
    **Tithi**, **Nakshatra**, **Yoga**, **Karana** e **Vara** (dia da semana),
    mais horários de nascer/pôr do sol e períodos auspiciosos/inauspiciosos.
    """
    if data:
        try:
            dt = datetime.strptime(data, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Formato de data inválido. Use YYYY-MM-DD (ex: 2026-05-12).",
            )
    else:
        dt = datetime.now()

    # ISO 8601 com fuso de Brasília (UTC-3). O '+' deve ser codificado como %2B na URL.
    dt_iso = dt.strftime("%Y-%m-%dT06:00:00-03:00")

    try:
        token = await obter_token()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Falha ao autenticar na Prokerala. Verifique CLIENT_ID e CLIENT_SECRET. ({e.response.status_code})",
        )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                PROKERALA_PANCHANG_URL,
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "ayanamsa": 1,          # Lahiri — padrão védico
                    "coordinates": f"{latitude},{longitude}",
                    "datetime": dt_iso,
                    "la": "en",
                },
            )
            r.raise_for_status()
            raw = r.json().get("data", {})
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Erro na API Prokerala: {e.response.status_code} — {e.response.text[:300]}",
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Timeout na conexão com a Prokerala.")

    # Períodos inauspiciosos relevantes (Rahu Kaal, Yamaganda, Gulika)
    periodos_inauspiciosos = [
        {
            "nome": p.get("name", ""),
            "inicio": p.get("period", [{}])[0].get("start", "") if p.get("period") else "",
            "fim": p.get("period", [{}])[0].get("end", "") if p.get("period") else "",
        }
        for p in raw.get("inauspicious_period", [])
        if p.get("name") in ("Rahu", "Yamaganda", "Gulika")
    ]

    # Períodos auspiciosos (Abhijit Muhurat, Amrit Kaal, Brahma Muhurat)
    periodos_auspiciosos = [
        {
            "nome": p.get("name", ""),
            "inicio": p.get("period", [{}])[0].get("start", "") if p.get("period") else "",
            "fim": p.get("period", [{}])[0].get("end", "") if p.get("period") else "",
        }
        for p in raw.get("auspicious_period", [])
    ]

    return {
        "data": dt.strftime("%d/%m/%Y"),
        "dia_semana": raw.get("vaara", ""),
        "tithi": {
            "nome": primeiro(raw.get("tithi"), "name"),
            "paksha": primeiro(raw.get("tithi"), "paksha"),
            "numero": primeiro(raw.get("tithi"), "id"),
        },
        "nakshatra": {
            "nome": primeiro(raw.get("nakshatra"), "name"),
            "regente": primeiro_aninhado(raw.get("nakshatra"), "lord", "name"),
        },
        "yoga": {
            "nome": primeiro(raw.get("yoga"), "name"),
        },
        "karana": {
            "nome": primeiro(raw.get("karana"), "name"),
        },
        "sol": {
            "nascer": raw.get("sunrise", ""),
            "por": raw.get("sunset", ""),
        },
        "lua": {
            "nascer": raw.get("moonrise", ""),
            "por": raw.get("moonset", ""),
        },
        "periodos_auspiciosos": periodos_auspiciosos,
        "periodos_inauspiciosos": periodos_inauspiciosos,
        "coordenadas": {"latitude": latitude, "longitude": longitude},
    }


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}
