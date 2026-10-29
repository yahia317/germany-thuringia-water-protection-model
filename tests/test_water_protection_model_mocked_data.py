import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon, box

import thuringia_water_protection_model as model
import os

@pytest.fixture
def sample_zones():
    """Representative protection-zone data created in memory."""
    return gpd.GeoDataFrame(
        {
            "Gebiet_Nr": [101.0, 102.0],
            "Gebietsart": ["Trinkwasser", "Heilquelle"],
            "Zone": ["II", None],
            "Status": ["festgesetzt", "festgesetzt"],
            "Url": [None, "https://example.test/zone/102"],
            "Stand": pd.to_datetime(["2026-10-13", "2026-10-13"]),
        },
        geometry=[
            box(0, 0, 10, 10),
            box(20, 20, 30, 30),
        ],
        crs="EPSG:25832",
    )


def test_map_zone_attributes(sample_zones):
    """Source fields map correctly; unavailable values are not invented."""
    mapped = model.map_zone_attributes(sample_zones)

    assert mapped["state_code"].tolist() == ["TH", "TH"]
    assert mapped["source_zone_id"].tolist() == [101.0, 102.0]
    assert mapped["zone_code"].tolist()[0] == "II"
    assert mapped["designation_status"].tolist() == [
        "festgesetzt",
        "festgesetzt",
    ]
    assert pd.isna(mapped["source_url"].iloc[0])

    # These values are not supplied by the source dataset.
    assert mapped["restriction_category"].isna().all()
    assert mapped["extraction_rate"].isna().all()


def test_detect_invalid_geometry():
    """A self-intersecting polygon is reported as invalid."""
    invalid_polygon = Polygon(
        [(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)]
    )
    zones = gpd.GeoDataFrame(
        { "Gebiet_Nr": [201.0], "Name": ["Invalid test zone"], "Zone": ["II"], "Status": ["festgesetzt"], },
        geometry=[invalid_polygon],
        crs="EPSG:25832",
    )

    report, details = model.validate_zone_geometries(zones)

    assert report["total_features"] == 1
    assert report["invalid_geometries"] == 1
    assert len(details) == 1


def test_repair_invalid_geometry():
    """Invalid geometry is repaired into a valid geometry."""
    invalid_polygon = Polygon(
        [(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)]
    )
    zones = gpd.GeoDataFrame(
        {"Gebiet_Nr": [202.0], "Name": ["Repair test zone"]},
        geometry=[invalid_polygon],
        crs="EPSG:25832",
    )

    repaired, repair_report = model.repair_invalid_geometries(zones)

    assert repaired.geometry.is_valid.all()
    assert len(repair_report) == 1
    assert not repair_report.iloc[0]["original_valid"]
    assert repair_report.iloc[0]["repaired_valid"]


def test_reject_missing_source_crs(sample_zones):
    """A missing CRS must not silently pass validation."""
    zones = sample_zones.set_crs(None, allow_override=True)

    with pytest.raises(ValueError, match="Source CRS is missing"):
        model.ensure_target_crs(zones)


def test_preserve_target_crs(sample_zones):
    """Data already in EPSG:25832 remains in that CRS."""
    result = model.ensure_target_crs(sample_zones)

    assert result.crs.to_epsg() == 25832


@pytest.mark.parametrize(
    ("parcel", "expected_intersections", "expected_containment"),
    [
        (box(2, 2, 4, 4), 1, 1),       # Fully inside
        (box(9, 9, 11, 11), 1, 0),     # Partial overlap
        (box(12, 12, 14, 14), 0, 0),   # Outside
    ],
)
def test_find_zones_for_parcel(
    sample_zones,
    parcel,
    expected_intersections,
    expected_containment,
):
    """Spatial queries distinguish intersections from containment."""
    zones = sample_zones.iloc[[0]].copy()

    result = model.find_zones_for_parcel(parcel, zones)

    assert len(result["intersecting_zones"]) == expected_intersections
    assert len(result["containing_zones"]) == expected_containment


def test_create_test_parcel_inside_zone():
    """The helper creates a valid parcel covered by its zone."""
    zone = box(0, 0, 100, 100)

    parcel = model.create_test_parcel(zone, size=10)

    assert zone.covers(parcel)
    assert parcel.is_valid
    assert parcel.area > 0


def test_reject_duplicate_source_keys_before_database_connection(
    sample_zones, monkeypatch
):
    """Duplicate source keys are rejected before connecting to PostGIS."""
    mapped = model.map_zone_attributes(sample_zones.iloc[[0]].copy())
    duplicate = gpd.GeoDataFrame(
        pd.concat([mapped, mapped], ignore_index=True),
        geometry="geometry",
        crs="EPSG:25832",
    )

    def unexpected_connect(*args, **kwargs):
        pytest.fail("The database should not be contacted by this test.")

    monkeypatch.setattr(model.psycopg, "connect", unexpected_connect)

    with pytest.raises(ValueError, match="Duplicate source zone IDs"):
        model.load_zones_to_postgis(
            duplicate,
            conninfo="postgresql://unused",
        )


def test_get_total_feature_count_with_mocked_wfs_response(monkeypatch):
    """Feature-count parsing works without a real WFS request."""

    class FakeResponse:
        content = (
            b'<wfs:FeatureCollection '
            b'xmlns:wfs="http://www.opengis.net/wfs/2.0" '
            b'numberMatched="3090"/>'
        )
        text = content.decode()

        def raise_for_status(self):
            pass

    def fake_get(url, **kwargs):
        assert url == model.WFS_URL
        assert kwargs["params"]["RESULTTYPE"] == "hits"
        return FakeResponse()

    monkeypatch.setattr(model.requests, "get", fake_get)

    assert model.get_total_feature_count() == 3090


@pytest.mark.integration
def test_postgis_load_is_idempotent():
    """Loading the same zone twice must leave one row, updated in place."""
    import psycopg

    conninfo = os.getenv("IRIS_TEST_DATABASE_URL")
    if not conninfo:
        pytest.skip(
            "Set IRIS_TEST_DATABASE_URL to a dedicated test database."
        )

    test_id = 987654321.0
    state_code = "ZZ"

    zones = gpd.GeoDataFrame(
        {
            "source_zone_id": [test_id],
            "state_code": [state_code],
            "zone_code": ["II"],
            "protection_type": ["TEST"],
            "designation_status": ["test"],
            "restriction_category": [None],
            "extraction_rate": [None],
            "source_url": [None],
            "source_updated_at": [pd.Timestamp("2026-10-13").date()],
        },
        geometry=[box(500000, 5700000, 500001, 5700001)],
        crs="EPSG:25832",
    )

    try:
        # Start with a clean test record.
        with psycopg.connect(conninfo) as connection:
            connection.execute(
                """
                DELETE FROM iris_staging.water_protection_zones
                WHERE source_zone_id = %s AND state_code = %s
                """,
                (test_id, state_code),
            )

        first = model.load_zones_to_postgis(zones, conninfo=conninfo)

        # Change a value to verify that the second load updates the row.
        zones.loc[zones.index[0], "zone_code"] = "III"
        second = model.load_zones_to_postgis(zones, conninfo=conninfo)

        with psycopg.connect(conninfo) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*), MIN(zone_code)
                FROM iris_staging.water_protection_zones
                WHERE source_zone_id = %s AND state_code = %s
                """,
                (test_id, state_code),
            ).fetchone()

        assert first["submitted"] == 1
        assert second["submitted"] == 1
        assert row[0] == 1
        assert row[1] == "III"

    finally:
        # Remove the test record even if an assertion fails.
        with psycopg.connect(conninfo) as connection:
            connection.execute(
                """
                DELETE FROM iris_staging.water_protection_zones
                WHERE source_zone_id = %s AND state_code = %s
                """,
                (test_id, state_code),
            )

