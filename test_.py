import thuringia_water_protection_model as model
from shapely.geometry import box
import geopandas as gpd



if __name__ == "__main__":

    # 1.1 inspect service metadata
    print("Inspecting service metadata...")
    print("Title and Layers:", model.inspect_service_metadata())
    print("Total available features:", model.get_total_feature_count())
    print("Field names:", model.get_field_names())
    model.download_zone_features()

    print("Occurrences of each distinct value for every field:")
    model.analyze_zone_fields() #must run after downloading the features to data/raw folder using the function exacly above this one


    # 1.2 retrieve zone geometries
    zones = model.retrieve_zone_geometries()
    print("Retrieved geometries:", len(zones))
    print("Source CRS:", zones.crs)# all geometries are in EPSG:25832 (ETRS89 / UTM zone 32N)


    # 1.3 retrieve water-protection zone geometries and legally described zone classes for the specified state.
    water_protection_zones = zones[zones["Gebiet"] == "WSG"].copy()
    print("Water protection zones:", len(water_protection_zones))
    print("Distinct water protection zone classes:",water_protection_zones["Zone"].nunique())
    print(sorted(water_protection_zones["Zone"].dropna().unique()))
    print("Sz_Num values:")
    print(
        water_protection_zones["Sz_Num"]
        .value_counts(dropna=False)
        .sort_index()
        .to_string()
    )


    # 2 Map official zone codes, restriction categories and extraction rates only where published; mark absent/unknown attributes explicitly.(<NA>) for absent/unknown
    mapped_zones = model.map_zone_attributes(water_protection_zones)
    print(mapped_zones[["source_zone_id","state_code","zone_code","protection_type","designation_status","restriction_category","extraction_rate",]].head().to_string(index=False))

    
    # 3.1 Validate zone geometries
    report, invalid_details = model.validate_zone_geometries(zones)

    print("\nGeometry validation report:")
    for key, value in report.items():
        print(f"{key}: {value}")

    print("\nInvalid geometries:")
    print(invalid_details.to_string(index=False))

    # 3.2 Zones are already in the EPSG:25832, but just to make sure, this function transforms the source CRS to EPSG:25832 if they weren't already.
    zones = model.ensure_target_crs(zones)

    print("CRS after validation:", zones.crs)
    assert zones.crs.to_epsg() == 25832


    # 3.3 Test zone-parcel intersection and containment
    test_zones = gpd.GeoDataFrame(
        {"source_zone_id": ["A"]},
        geometry=[box(0, 0, 10, 10)],
        crs="EPSG:25832",
    )

    test_cases = [
        ("inside", box(2, 2, 4, 4), 1, 1),
        ("partial overlap", box(9, 9, 11, 11), 1, 0),
        ("outside", box(12, 12, 14, 14), 0, 0),
    ]

    for name, parcel, expected_intersections, expected_containment in test_cases:
        result = model.find_zones_for_parcel(parcel, test_zones)

        assert len(result["intersecting_zones"]) == expected_intersections, name
        assert len(result["containing_zones"]) == expected_containment, name

    print("All three parcel spatial tests passed.")

    
    # 3.4 Repair invalid geometries
    zones, repair_report = model.repair_invalid_geometries(zones)

    print("\nGeometry repair report:")
    if repair_report.empty:
        print("No repairs were needed.")
    else:
        print(repair_report.to_string(index=False))

    # Confirm all non-empty geometries are now valid.
    invalid_remaining = (
        zones.geometry.notna()
        & ~zones.geometry.is_empty
        & ~zones.geometry.is_valid
    ).sum()

    print("Invalid geometries remaining:", invalid_remaining)
    assert invalid_remaining == 0

    # 4 Load idempotently into iris_staging.water_protection_zones keyed by source zone ID and state code.
    mapped_zones = model.map_zone_attributes(
        zones[zones["Gebiet"] == "WSG"].copy()
    )

    result = model.load_zones_to_postgis(mapped_zones)
    print("PostGIS load result:", result)

    # 5 Record data origin, update dates, empty/error responses and coverage limitations.
    model.record_data_quality(zones=zones,repair_report=repair_report)

    
    