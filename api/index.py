import os
import math
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    DateTime,
    desc,
    delete,
)
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./conversions.db")
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class ConversionHistory(Base):
    __tablename__ = "conversion_history"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String(20), nullable=False)
    from_unit = Column(String(30), nullable=False)
    to_unit = Column(String(30), nullable=False)
    input_value = Column(Float, nullable=False)
    result = Column(Float, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


Base.metadata.create_all(bind=engine)


LENGTH = {
    "m": 1.0,
    "km": 1000.0,
    "cm": 0.01,
    "mm": 0.001,
    "mi": 1609.344,
    "yd": 0.9144,
    "ft": 0.3048,
    "in": 0.0254,
}

MASS = {
    "kg": 1.0,
    "g": 0.001,
    "mg": 0.000001,
    "t": 1000.0,
    "lb": 0.45359237,
    "oz": 0.028349523125,
}

AREA = {
    "m2": 1.0,
    "km2": 1_000_000.0,
    "cm2": 0.0001,
    "ha": 10000.0,
    "ac": 4046.8564224,
    "ft2": 0.09290304,
}

VOLUME = {
    "l": 1.0,
    "ml": 0.001,
    "m3": 1000.0,
    "gal": 3.785411784,
    "qt": 0.946352946,
    "pt": 0.473176473,
    "cup": 0.2365882365,
}

TEMPERATURE_UNITS = ("c", "f", "k")

REGISTRIES = {
    "length": LENGTH,
    "mass": MASS,
    "temperature": {u: 1.0 for u in TEMPERATURE_UNITS},
    "area": AREA,
    "volume": VOLUME,
}

CATEGORIES = set(REGISTRIES.keys())

UNIT_LABELS = {
    "length": {
        "m": "Meter",
        "km": "Kilometer",
        "cm": "Centimeter",
        "mm": "Millimeter",
        "mi": "Mile",
        "yd": "Yard",
        "ft": "Foot",
        "in": "Inch",
    },
    "mass": {
        "kg": "Kilogram",
        "g": "Gram",
        "mg": "Milligram",
        "t": "Metric Ton",
        "lb": "Pound",
        "oz": "Ounce",
    },
    "temperature": {
        "c": "Celsius",
        "f": "Fahrenheit",
        "k": "Kelvin",
    },
    "area": {
        "m2": "Square Meter",
        "km2": "Square Kilometer",
        "cm2": "Square Centimeter",
        "ha": "Hectare",
        "ac": "Acre",
        "ft2": "Square Foot",
    },
    "volume": {
        "l": "Liter",
        "ml": "Milliliter",
        "m3": "Cubic Meter",
        "gal": "Gallon (US)",
        "qt": "Quart (US)",
        "pt": "Pint (US)",
        "cup": "Cup (US)",
    },
}


def round6(value: float) -> float:
    return round(value + 0.0, 6)


def convert_temperature(value: float, from_unit: str, to_unit: str):
    if from_unit == to_unit:
        return value, f"{value} \u00b0{from_unit.upper()} = {value} \u00b0{to_unit.upper()}"

    if from_unit == "c":
        celsius = value
    elif from_unit == "f":
        celsius = (value - 32.0) * 5.0 / 9.0
    elif from_unit == "k":
        celsius = value - 273.15
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported temperature unit: {from_unit}")

    if to_unit == "c":
        result = celsius
    elif to_unit == "f":
        result = celsius * 9.0 / 5.0 + 32.0
    elif to_unit == "k":
        result = celsius + 273.15
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported temperature unit: {to_unit}")

    formula = (
        f"{value} {from_unit.upper()} \u2192 "
        f"{round(result, 6)} {to_unit.upper()} "
        f"(via Celsius: {round(celsius, 6)}\u00b0C)"
    )
    return result, formula


def convert_generic(value: float, category: str, from_unit: str, to_unit: str):
    table = REGISTRIES[category]
    base_value = value * table[from_unit]
    result = base_value / table[to_unit]
    formula = (
        f"{value} {from_unit} = {round(result, 6)} {to_unit} "
        f"(base coefficient: {table[from_unit]} / {table[to_unit]})"
    )
    return result, formula


class ConversionRequest(BaseModel):
    category: str
    from_unit: str
    to_unit: str
    value: float = Field(..., gt=-1e15, lt=1e15)

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in CATEGORIES:
            raise ValueError(
                f"Invalid category '{v}'. Allowed: {sorted(CATEGORIES)}"
            )
        return v

    @field_validator("from_unit", "to_unit")
    @classmethod
    def normalize_unit(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("value")
    @classmethod
    def validate_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("value must be a finite number")
        return v


class ConversionResponse(BaseModel):
    result: float
    formula: str
    id: int


class HistoryItem(BaseModel):
    id: int
    category: str
    from_unit: str
    to_unit: str
    input_value: float
    result: float
    created_at: datetime


class DeleteResponse(BaseModel):
    deleted: int


class UnitEntry(BaseModel):
    value: str
    label: str


class UnitsResponse(BaseModel):
    units: dict


app = FastAPI(title="Conversion API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN] if FRONTEND_ORIGIN != "*" else ["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.post("/api/conversions", response_model=ConversionResponse)
def create_conversion(payload: ConversionRequest):
    category = payload.category
    from_unit = payload.from_unit
    to_unit = payload.to_unit
    value = payload.value

    table = REGISTRIES[category]
    if from_unit not in table:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid from_unit '{from_unit}' for category '{category}'. Allowed: {sorted(table)}",
        )
    if to_unit not in table:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid to_unit '{to_unit}' for category '{category}'. Allowed: {sorted(table)}",
        )
    if not math.isfinite(value) or value <= -1e15 or value >= 1e15:
        raise HTTPException(status_code=400, detail="value must be finite and within (-1e15, 1e15)")

    if category == "temperature":
        result, formula = convert_temperature(value, from_unit, to_unit)
    else:
        result, formula = convert_generic(value, category, from_unit, to_unit)

    result = round6(result)

    db = SessionLocal()
    try:
        record = ConversionHistory(
            category=category,
            from_unit=from_unit,
            to_unit=to_unit,
            input_value=value,
            result=result,
            created_at=datetime.now(timezone.utc),
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return ConversionResponse(result=result, formula=formula, id=record.id)
    finally:
        db.close()


@app.get("/api/history", response_model=list[HistoryItem])
def get_history(limit: int = Query(default=50, ge=1, le=1000)):
    db = SessionLocal()
    try:
        rows = (
            db.query(ConversionHistory)
            .order_by(desc(ConversionHistory.created_at), desc(ConversionHistory.id))
            .limit(limit)
            .all()
        )
        return [
            HistoryItem(
                id=r.id,
                category=r.category,
                from_unit=r.from_unit,
                to_unit=r.to_unit,
                input_value=r.input_value,
                result=r.result,
                created_at=r.created_at,
            )
            for r in rows
        ]
    finally:
        db.close()


@app.delete("/api/history", response_model=DeleteResponse)
def clear_history():
    db = SessionLocal()
    try:
        deleted = db.query(ConversionHistory).delete()
        db.commit()
        return DeleteResponse(deleted=int(deleted or 0))
    finally:
        db.close()


@app.get("/api/units", response_model=UnitsResponse)
def get_units():
    grouped = {}
    for category in ["length", "mass", "temperature", "area", "volume"]:
        labels = UNIT_LABELS.get(category, {})
        grouped[category] = [
            UnitEntry(value=unit, label=labels.get(unit, unit))
            for unit in REGISTRIES[category].keys()
        ]
    return UnitsResponse(units=grouped)


@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
