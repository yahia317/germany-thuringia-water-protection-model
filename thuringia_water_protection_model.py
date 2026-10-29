
from io import BytesIO
from logging import root

import geopandas as gpd
import requests
from owslib.wfs import WebFeatureService

from xml.etree import ElementTree as ET

from pathlib import Path
import requests
from collections import Counter

import warnings
warnings.filterwarnings("ignore", message=r"Field with same name \(name\) already exists.*")

import pandas as pd
from shapely.validation import explain_validity
from shapely.geometry import box

from shapely import make_valid

import os
import psycopg

import json
from datetime import datetime, timezone


WFS_URL = "http://www.geoproxy.geoportal-th.de/geoproxy/services/wsg_hqsg_wfs"
LAYER = "tmlnu:WASSER_UND_HEILQUELLENSCHUTZGEBIETE"
STATE_CODE = "TH"


def get_total_feature_count():
    """Return the WFS feature count or raise a clear error."""
    response = requests.get(
        WFS_URL,
        params={
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": LAYER,
            "RESULTTYPE": "hits",
        },
        timeout=60,
    )
    response.raise_for_status()

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise RuntimeError("WFS returned malformed XML for feature count.") from exc

    if any(
        name in {"ExceptionReport", "ServiceExceptionReport"}
        for name in (element.tag.split("}")[-1] for element in root.iter())
    ):
        raise RuntimeError(
            f"WFS returned an exception instead of a feature count: "
            f"{response.text[:1000]}"
        )

    count = root.attrib.get("numberMatched")
    if count is None or not count.isdigit():
        raise RuntimeError(
            f"WFS returned no usable numberMatched value: {count!r}"
        )

    return int(count)


def get_field_names():
    """Return all field names published in the WFS layer schema."""

    response = requests.get(
        WFS_URL,
        params={
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "DescribeFeatureType",
            "TYPENAMES": LAYER,
        },
        timeout=30,
    )
    response.raise_for_status()

    root = ET.fromstring(response.content)

    excluded_fields = 2 # the first two excluded as they come from XML schema definitions, not actual data fields.
    return [
        element.attrib["name"]
        for element in root.iter()
        if element.tag.endswith("element") and "name" in element.attrib
    ][excluded_fields:]


# get the service metadata Title and Layer information
def inspect_service_metadata():
    """Inspect the Thuringia WFS service metadata."""
    response = requests.get(
        WFS_URL,
        params={
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetCapabilities",
        },
        timeout=60,
    )

    response.raise_for_status()

    from xml.etree import ElementTree as ET

    root = ET.fromstring(response.content)
    namespaces = {
        "ows": "http://www.opengis.net/ows/1.1",
        "wfs": "http://www.opengis.net/wfs/2.0",
    }

    title = root.findtext(".//ows:ServiceIdentification/ows:Title",
                          namespaces=namespaces)

    layers = [
        element.text
        for element in root.findall(".//wfs:FeatureType/wfs:Name",
                                    namespaces=namespaces)
        if element.text
    ]

    return {"title": title, "layers": layers}


def download_zone_features(total=None, page_size=100, output_dir="data"):
    """Download WFS features and record provenance, coverage and errors."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = output_dir / "retrieval_manifest.json"
    retrieved_at = datetime.now(timezone.utc).isoformat()

    manifest = {
        "status": "running",
        "retrieved_at_utc": retrieved_at,
        "authority": "Thuringian State Office for the Environment, Mining and Nature Conservation (TLUBN)",
        "source_url": WFS_URL,
        "layer": LAYER,
        "state_code": STATE_CODE,
        "source_crs": "EPSG:25832",
        "target_crs": "EPSG:25832",
        "licence": "CC BY 4.0",
        "attribution": "© GDI-Th",
        "page_size": page_size,
        "expected_features": None,
        "downloaded_features": 0,
        "pages": [],
        "empty_response": False,
        "errors": [],
        "coverage_limitations": [
            "The source layer contains both WSG and HQSG features.",
            "The current database load includes WSG features only; HQSG features are excluded.",
            "Restriction categories and extraction rates are not published in the inspected WFS fields.",
            "The source-reported Stand date is distinct from this retrieval timestamp.",
        ],
    }

    def save_manifest():
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    try:
        if page_size <= 0:
            raise ValueError("page_size must be greater than zero.")

        if total is None:
            total = get_total_feature_count()

        if total < 0:
            raise ValueError("Expected feature count cannot be negative.")

        manifest["expected_features"] = total

        if total == 0:
            manifest["status"] = "empty"
            manifest["empty_response"] = True
            save_manifest()
            raise RuntimeError(
                "The WFS reports zero features. The empty result was recorded; "
                "existing fixture files were not removed."
            )

        downloaded = 0

        with requests.Session() as session:
            for start in range(0, total, page_size):
                response = session.get(
                    WFS_URL,
                    params={
                        "SERVICE": "WFS",
                        "VERSION": "2.0.0",
                        "REQUEST": "GetFeature",
                        "TYPENAMES": LAYER,
                        "COUNT": page_size,
                        "STARTINDEX": start,
                    },
                    timeout=120,
                )
                response.raise_for_status()

                try:
                    root = ET.fromstring(response.content)
                except ET.ParseError as exc:
                    raise RuntimeError(
                        f"Malformed XML in page starting at {start}."
                    ) from exc

                local_names = [
                    element.tag.split("}")[-1]
                    for element in root.iter()
                ]

                if any(
                    name in {"ExceptionReport", "ServiceExceptionReport"}
                    for name in local_names
                ):
                    raise RuntimeError(
                        f"WFS exception in page starting at {start}: "
                        f"{response.text[:1000]}"
                    )

                members = sum(name == "member" for name in local_names)
                expected_page_count = min(page_size, total - start)

                if members == 0:
                    manifest["empty_response"] = True
                    raise RuntimeError(
                        f"Empty WFS page at STARTINDEX={start}; "
                        f"expected {expected_page_count} features."
                    )

                if members != expected_page_count:
                    raise RuntimeError(
                        f"Incomplete WFS page at STARTINDEX={start}: "
                        f"expected {expected_page_count}, received {members}."
                    )

                output_file = output_dir / f"features_{start:04d}.xml"
                output_file.write_bytes(response.content)

                downloaded += members
                manifest["downloaded_features"] = downloaded
                manifest["pages"].append({
                    "start_index": start,
                    "feature_count": members,
                    "http_status": response.status_code,
                    "file": output_file.name,
                    "bytes": len(response.content),
                })
                save_manifest()

                print(
                    f"Saved {output_file} "
                    f"({members} features, {len(response.content):,} bytes)"
                )

        if downloaded != total:
            raise RuntimeError(
                f"Download incomplete: expected {total}, received {downloaded}."
            )

        manifest["status"] = "success"
        manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        save_manifest()

        print(f"Download complete: {downloaded} features.")
        print(f"Retrieval manifest: {manifest_path}")

        return manifest

    except Exception as exc:
        if manifest["status"] != "empty":
            manifest["status"] = "failed"

        manifest["errors"].append({
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "type": type(exc).__name__,
            "message": str(exc),
        })
        save_manifest()
        raise


def analyze_zone_fields(folder="data"):
    """Analyze field values, distinct counts, and missing values in downloaded XML."""
    ns = {
        "wfs": "http://www.opengis.net/wfs/2.0",
        "tmlnu": "http://www.geoproxy.geoportal-th.de/tmlnu",
    }

    fields = [
        "Gebiet_Nr", "Gebiet", "Sg_Id", "Name", "Gebietsart",
        "Zone", "Status", "Sz_Num", "Bemerkung", "Symbolik",
        "Url", "Scale", "Herkunft", "Stand", "geom",
    ]

    values = {field: Counter() for field in fields}
    missing = Counter()
    total = 0

    for file in sorted(Path(folder).glob("features_*.xml")):
        root = ET.parse(file).getroot()

        for member in root.findall("wfs:member", ns):
            feature = member.find(
                "tmlnu:WASSER_UND_HEILQUELLENSCHUTZGEBIETE", ns
            )
            if feature is None:
                continue

            total += 1
            for field in fields:
                value = feature.findtext(
                    f"tmlnu:{field}", default="", namespaces=ns
                ).strip()

                if value:
                    values[field][value] += 1
                else:
                    missing[field] += 1

    print("Total features:", total)

    for field in fields:
        print(
            f"\n{field} — distinct values: {len(values[field])}; "
            f"missing: {missing[field]}"
        )
        for value, count in values[field].most_common(20):
            print(f"  {value!r}: {count}")

    return {"total": total, "values": values, "missing": missing}


def retrieve_zone_geometries(folder="data"):
    """Load water-protection zone geometries for Thuringia from downloaded WFS files."""
    import geopandas as gpd
    import pandas as pd
    from pathlib import Path

    files = sorted(Path(folder).glob("features_*.xml"))
    if not files:
        raise FileNotFoundError(f"No WFS XML files found in {folder}")

    frames = [gpd.read_file(file) for file in files]
    frames = [frame for frame in frames if not frame.empty]

    if not frames:
        raise ValueError("The downloaded WFS files contain no features.")

    zones = gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True),
        geometry="geometry",
        crs=frames[0].crs,
    )

    return zones


def retrieve_zone_classes(zones):
    """Extract published zone classifications and official reference URLs."""
    fields = [
        "Gebiet_Nr",
        "Gebiet",
        "Name",
        "Gebietsart",
        "Zone",
        "Status",
        "Bemerkung",
        "Url",
    ]

    missing_fields = [field for field in fields if field not in zones.columns]
    if missing_fields:
        raise ValueError(f"Missing expected source fields: {missing_fields}")

    return zones[fields].drop_duplicates().reset_index(drop=True)


def map_zone_attributes(zones):
    """Map official WFS attributes without inferring legal restrictions."""

    mapped = zones.copy()

    mapped["state_code"] = "TH"
    mapped["source_zone_id"] = mapped["Gebiet_Nr"]
    mapped["zone_code"] = mapped["Zone"]
    mapped["protection_type"] = mapped["Gebietsart"]
    mapped["designation_status"] = mapped["Status"]
    mapped["source_url"] = mapped["Url"]
    mapped["source_updated_at"] = mapped["Stand"]

    # These attributes are not explicitly provided by the inspected WFS schema.
    mapped["restriction_category"] = pd.NA
    mapped["extraction_rate"] = pd.NA

    return mapped


def validate_zone_geometries(zones):
    geometry = zones.geometry

    invalid_mask = (
        geometry.notna()
        & ~geometry.is_empty
        & ~geometry.is_valid
    )

    invalid_details = zones.loc[
        invalid_mask,
        ["Gebiet_Nr", "Name", "Zone", "Status"]
    ].copy()

    invalid_details["geometry_issue"] = geometry.loc[
        invalid_mask
    ].map(explain_validity)

    report = {
        "total_features": len(zones),
        "missing_geometries": int(geometry.isna().sum()),
        "empty_geometries": int(geometry.is_empty.sum()),
        "invalid_geometries": int(invalid_mask.sum()),
        "multipart_polygons": int(
            (geometry.geom_type == "MultiPolygon").sum()
        ),
        "geometry_types": geometry.geom_type.value_counts(
            dropna=False
        ).to_dict(),
    }

    return report, invalid_details


def ensure_target_crs(zones, target_crs="EPSG:25832"):
    """Ensure geometries use the target CRS."""
    if zones.crs is None:
        raise ValueError("Source CRS is missing; cannot transform safely.")

    if zones.crs.to_string() != target_crs:
        zones = zones.to_crs(target_crs)

    return zones


def create_test_parcel(zone, size=10.0):
    """
    Create a synthetic square parcel fully inside a valid zone.

    Parameters:
        zone: Shapely Polygon or MultiPolygon.
        size: Initial parcel side length in metres (EPSG:25832).

    Returns:
        A Shapely Polygon guaranteed to be covered by the zone.
    """
    if zone is None or zone.is_empty:
        raise ValueError("Zone geometry is missing or empty.")

    if not zone.is_valid:
        raise ValueError("Cannot create a test parcel from an invalid zone.")

    if zone.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError("Zone must be a Polygon or MultiPolygon.")

    if size <= 0:
        raise ValueError("Parcel size must be greater than zero.")

    # A representative point lies inside the actual polygon,
    # unlike the centre of its bounding box.
    point = zone.representative_point()
    current_size = size

    # Shrink the square until the entire parcel fits inside the zone.
    for _ in range(30):
        half = current_size / 2
        parcel = box(
            point.x - half,
            point.y - half,
            point.x + half,
            point.y + half,
        )

        if zone.covers(parcel):
            return parcel

        current_size /= 2

    raise ValueError(
        "Could not create a parcel fully inside the zone. "
        "Try a different zone."
    )


def find_zones_for_parcel(parcel, zones):
    """
    Find zones that intersect a parcel and zones that fully cover it.

    The parcel coordinates are assumed to use the same CRS as zones.
    """
    if zones.crs is None:
        raise ValueError("Zone CRS is missing.")

    if parcel is None or parcel.is_empty or not parcel.is_valid:
        raise ValueError("Parcel geometry is missing, empty, or invalid.")

    if zones.geometry.isna().any():
        raise ValueError("Some zones have missing geometries.")

    valid_geometry = zones.geometry.notna() & ~zones.geometry.is_empty

    intersects_mask = valid_geometry & zones.geometry.intersects(parcel)
    contains_mask = valid_geometry & zones.geometry.covers(parcel)

    return {
        "intersecting_zones": zones.loc[intersects_mask].copy(),
        "containing_zones": zones.loc[contains_mask].copy(),
    }


def repair_invalid_geometries(zones):
    """Repair invalid geometries and report any changes."""
    repaired = zones.copy()
    invalid_mask = (
        repaired.geometry.notna()
        & ~repaired.geometry.is_empty
        & ~repaired.geometry.is_valid
    )

    repair_records = []

    for index in repaired.index[invalid_mask]:
        original = repaired.at[index, repaired.geometry.name]
        fixed = make_valid(original)

        repair_records.append({
            "source_zone_id": repaired.at[index, "Gebiet_Nr"],
            "name": repaired.at[index, "Name"],
            "original_type": original.geom_type,
            "repaired_type": fixed.geom_type,
            "original_valid": original.is_valid,
            "repaired_valid": fixed.is_valid,
        })

        repaired.at[index, repaired.geometry.name] = fixed

    repair_report = pd.DataFrame(repair_records)

    return repaired, repair_report


def load_zones_to_postgis(zones, conninfo=None, batch_size=500):
    """Idempotently load mapped protection zones into PostGIS."""

    required_columns = [
        "source_zone_id",
        "state_code",
        "zone_code",
        "protection_type",
        "designation_status",
        "restriction_category",
        "extraction_rate",
        "source_url",
        "source_updated_at",
    ]

    missing = [column for column in required_columns if column not in zones.columns]
    if missing:
        raise ValueError(f"Missing mapped columns: {missing}")

    if zones.crs is None or zones.crs.to_epsg() != 25832:
        raise ValueError("Zones must use CRS EPSG:25832.")

    if zones.geometry.isna().any() or zones.geometry.is_empty.any():
        raise ValueError("Zones contain missing or empty geometries.")

    if not zones.geometry.is_valid.all():
        raise ValueError("Repair invalid geometries before loading.")

    if zones.duplicated(["source_zone_id", "state_code"]).any():
        raise ValueError("Duplicate source zone IDs for the same state.")

    if conninfo is None:
        conninfo = os.getenv(
            "IRIS_DATABASE_URL",
            "postgresql://iris:iris_dev_password@localhost:5432/iris",
        )

    sql = """
        INSERT INTO iris_staging.water_protection_zones (
            source_zone_id, state_code, zone_code, protection_type,
            designation_status, restriction_category, extraction_rate,
            source_url, source_updated_at, geom
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s,
            ST_GeomFromWKB(%s, 25832)
        )
        ON CONFLICT (source_zone_id, state_code)
        DO UPDATE SET
            zone_code = EXCLUDED.zone_code,
            protection_type = EXCLUDED.protection_type,
            designation_status = EXCLUDED.designation_status,
            restriction_category = EXCLUDED.restriction_category,
            extraction_rate = EXCLUDED.extraction_rate,
            source_url = EXCLUDED.source_url,
            source_updated_at = EXCLUDED.source_updated_at,
            geom = EXCLUDED.geom,
            loaded_at = NOW()
    """

    records = []

    for _, row in zones.iterrows():
        def db_value(value):
            return None if pd.isna(value) else value

        records.append((
            db_value(row["source_zone_id"]),
            db_value(row["state_code"]),
            db_value(row["zone_code"]),
            db_value(row["protection_type"]),
            db_value(row["designation_status"]),
            db_value(row["restriction_category"]),
            db_value(row["extraction_rate"]),
            db_value(row["source_url"]),
            db_value(row["source_updated_at"]),
            row.geometry.wkb,
        ))

    affected = 0

    with psycopg.connect(conninfo) as connection:
        with connection.cursor() as cursor:
            for start in range(0, len(records), batch_size):
                batch = records[start:start + batch_size]
                cursor.executemany(sql, batch)
                affected += cursor.rowcount

    return {
        "submitted": len(records),
        "affected": affected,
    }


def record_data_quality(zones, repair_report=None):
    """Print provenance, source dates, coverage limitations and data quality."""

    source_dates = (
        sorted(
            pd.to_datetime(zones["Stand"], errors="coerce")
            .dropna()
            .dt.strftime("%Y-%m-%d")
            .unique()
            .tolist()
        )
        if "Stand" in zones.columns
        else []
    )

    counts = (
        zones["Gebiet"].fillna("UNKNOWN").value_counts().to_dict()
        if "Gebiet" in zones.columns
        else {}
    )

    wsg = zones[zones["Gebiet"] == "WSG"] if "Gebiet" in zones.columns else zones.iloc[0:0]

    print("\n=== DATA PROVENANCE AND QUALITY ===")
    print(f"Authority: TLUBN, Thuringia")
    print(f"Source URL: {WFS_URL}")
    print(f"Layer: {LAYER}")
    print("Licence: CC BY 4.0; attribution: © GDI-Th")
    print(f"Source update date(s): {source_dates or 'Unknown'}")

    manifest_path = Path(__file__).resolve().parent / "data" / "retrieval_manifest.json"

    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        print(f"Actual retrieval timestamp (UTC): {manifest.get('retrieved_at_utc', 'Unknown')}")
        print(f"Retrieval status: {manifest.get('status', 'Unknown')}")
        print(f"Downloaded features: {manifest.get('downloaded_features', 'Unknown')}")
        print(f"Empty response detected: {manifest.get('empty_response', 'Unknown')}")
        print(f"Retrieval errors: {manifest.get('errors', [])}")
    else:
        print("Retrieval timestamp and download errors: unavailable (manifest not found).")
        
    print(f"Feature counts by source type: {counts}")
    print(f"WSG features: {len(wsg)}")
    print(f"WSG zone codes: {sorted(wsg['Zone'].dropna().astype(str).unique()) if 'Zone' in wsg.columns else []}")

    for field in ("Herkunft", "Bemerkung", "Status", "Url", "Stand"):
        if field in zones.columns:
            print(f"Missing {field}: {int(zones[field].isna().sum())}/{len(zones)}")

    print("Restriction categories: unavailable in inspected source fields; not inferred.")
    print("Extraction rates: unavailable in inspected source fields; not inferred.")
    print("Coverage: source includes WSG and HQSG; current database load targets WSG only.")
    print("Retrieval history: only the latest download attempt is documented by the manifest.")

    if repair_report is not None:
        print(f"Geometry repairs performed: {len(repair_report)}")
        if not repair_report.empty:
            print(repair_report.to_string(index=False))

    return {
        "source_update_dates": source_dates,
        "feature_counts_by_type": counts,
        "wsg_feature_count": len(wsg),
        "geometry_repairs": len(repair_report) if repair_report is not None else None,
    }


