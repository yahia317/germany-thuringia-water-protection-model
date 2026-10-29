# Data Quality and Error Report

**Project:** Thuringia Water Protection Zones — WFS Ingestion  
**Source authority:** Thuringian State Office for the Environment, Mining and Nature Conservation (TLUBN)  
**WFS endpoint:** `http://www.geoproxy.geoportal-th.de/geoproxy/services/wsg_hqsg_wfs`  
**WFS layer:** `tmlnu:WASSER_UND_HEILQUELLENSCHUTZGEBIETE`  
**State code:** `TH`  
**Source CRS:** EPSG:25832  
**Target CRS:** EPSG:25832  
**Licence:** CC BY 4.0 — attribution: © GDI-Th  
**Target table:** `iris_staging.water_protection_zones`

## 1. Retrieval Results

The latest recorded WFS retrieval completed successfully. The retrieval manifest records 3,090 downloaded features, no reported retrieval errors, and no empty response.

| Result | Count |
|---|---:|
| Total retrieved source features | 3,090 |
| Water protection areas (WSG) | 3,050 |
| Mineral spring protection areas (HQSG) | 40 |
| Features targeted for database loading | 3,050 |

The source layer contains both water protection areas (WSG) and mineral spring protection areas (HQSG). The current ingestion pipeline intentionally filters for WSG features before loading them into the target table.

The endpoint and layer above identify the service used by the implementation. The retrieval manifest provides a record of the latest retrieval attempt and its reported outcome.

## 2. Geometry Validation and Repair

Initial validation identified one invalid source geometry:

- **Feature:** WSG Bretleben
- **Source identifier:** `Gebiet_Nr = 16428`
- **Zone:** II
- **Status:** festgesetzt
- **Issue:** Ring self-intersection at approximately `[654977.8, 5688617.769]`
- **Repair method:** Shapely `make_valid`
- **Outcome:** The repaired geometry passed validity checks and was reported as a Polygon.

The repair was performed in memory. The downloaded source XML files were not modified.

PostGIS validation following the verified loading run reported:

| Check | Result |
|---|---:|
| Loaded WSG rows | 3,050 |
| Null or empty geometries | 0 |
| Invalid geometries | 0 |
| Geometries with incorrect SRID | 0 |
| Distinct composite keys | 3,050 |

No features were reported as rejected during the verified loading run. The identified invalid geometry was repaired rather than rejected.

## 3. Attribute Completeness and Mapping

The following missing values were observed in the retrieved source data:

| Source field | Missing values |
|---|---:|
| `Herkunft` | 3,090 |
| `Bemerkung` | 2,440 |
| `Status` | 0 |
| `Url` | 58 |
| `Stand` | 0 |

The implementation maps official classifications from explicit source attributes, including zone code, protection type, designation status, source URL, and source update date. It does not infer official classifications through fragile substring matching.

Restriction categories and extraction rates were not available in the inspected source attributes. The corresponding target columns are therefore left `NULL` rather than inferred from zone codes or unrelated remarks.

Missing source values and unavailable target attributes are data limitations. Their absence does not establish that the underlying legal restrictions do not exist.

## 4. Duplicate-Load and Idempotency Validation

The target table uses `(source_zone_id, state_code)` as its composite primary key. The loader uses database conflict handling to update existing records rather than insert duplicate records for the same key.

Two validation results were obtained:

- **Manual real-data validation:** Repeated loading was checked against the WSG dataset. The database contained 3,050 rows and 3,050 distinct composite keys after the tested repeated loads.
- **Automated integration test:** A synthetic zone was loaded twice into the separate `iris_test` database. Its zone code was changed from `II` to `III` before the second load. The test confirmed that exactly one record remained and that its zone code was updated to `III`.

The automated integration test makes the repeated-load behavior reproducible for the tested scenario. These results do not establish correctness for every possible database failure or concurrent-load scenario.

## 5. Spatial Validation

Three synthetic parcel scenarios passed:

1. A parcel located inside a protection zone.
2. A parcel partially overlapping a protection zone.
3. A parcel located outside the tested zone.

These tests provide representative evidence that the parcel-zone query logic handles the tested containment and intersection relationships. They do not establish exhaustive correctness for every geometry, boundary condition, or real-world parcel.

## 6. Automated Test Results

The complete automated test suite passed: **12 passed in 1.55 seconds**.

Coverage includes:

- Source-attribute mapping and preservation of unavailable attributes.
- Invalid geometry detection and repair.
- CRS validation and preservation.
- Parcel-to-zone spatial queries for inside, partial-overlap, and outside cases.
- Duplicate source-key rejection before database access.
- WFS feature-count retrieval using a mocked response.
- PostGIS integration testing for repeated-load idempotency.

The suite combines synthetic fixtures and mocked responses with a database integration test. It does not independently revalidate every feature in the 3,050-row source dataset.

## 7. Limitations and Follow-up Considerations

- `Herkunft` is absent from all retrieved features, and `Bemerkung` is frequently missing.
- Restriction categories and extraction rates cannot be populated reliably from the inspected source attributes.
- The retrieval manifest records the latest retrieval attempt, not a complete historical record of all retrieval attempts.
- Geometry repair is performed in memory; the downloaded source files remain unchanged.
- Spatial tests cover representative synthetic cases, not exhaustive boundary and topology scenarios.
- Automated tests should be rerun after relevant code or dependency changes.
- Source-layer availability, schema, and legal attribute completeness should be rechecked when the upstream service changes.

## 8. Overall Assessment

The verified retrieval and loading run passed the reported geospatial integrity and duplicate-key checks. One invalid source geometry was identified and repaired, while missing and unavailable attributes were documented rather than guessed.

The automated test suite passed all 12 tests, including the PostGIS repeated-load integration test. Together with the manual real-data validation, these results provide evidence that the implementation meets the tested ingestion, geometry-validation, spatial-query, and idempotency requirements.

The results support the tested scenarios; they do not establish exhaustive spatial correctness or legal completeness.
