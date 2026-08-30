# Data sources and citations

Project: **ohw26_proj_eutrophos** - Mapping oxygen levels around Vancouver Island
(OceanHackWeek 2026, Bamfield Marine Sciences Centre).
Repo: https://github.com/oceanhackweek/ohw26_proj_eutrophos

> Record the **access date** for every source when you write the final report -
> all data below were retrieved via public APIs in August 2026.

## File -> source map

| File in `data/derived/` | Source | Access point |
|---|---|---|
| `site_daily.csv` | Ocean Networks Canada (cabled observatories + moorings) | Oceans 3.0 API |
| `cf_casts.csv` | ONC Community Fishers program (CTD casts) | Oceans 3.0 API |
| `dfo_casts.csv` | DFO Institute of Ocean Sciences rosette CTD casts | CIOOS Pacific ERDDAP, `IOS_CTD_Profiles` |
| `dfo_moorings_daily.csv` | DFO BC Shelf Mooring Program (A1, E01, ...) | CIOOS Pacific ERDDAP, `IOS_CTD_Moorings` |
| `bc_lighthouses_daily.csv` | DFO BC Shore Station Oceanographic Program (lightstations, since 1914) | CIOOS Pacific ERDDAP, `BCSOP_daily` |
| `era5_forcing_points.csv` | ECMWF ERA5 reanalysis, 6 marine cells, 2006-2026 | Copernicus Climate Data Store, `reanalysis-era5-single-levels-timeseries` |
| `fraser_discharge_daily.csv` | ECCC Water Survey of Canada, station 08MF005 (Fraser River at Hope) | MSC GeoMet OGC API, `hydrometric-daily-mean` |
| `ooi_oxygen_daily.csv` | NSF Ocean Observatories Initiative, Coastal Endurance (WA line) | OOI Data Explorer ERDDAP |
| `gebco_2026_*.nc` | GEBCO 2026 global bathymetry grid | GEBCO / BODC download service |
| `site_classification.csv`, `site_forcing_assignment.csv`, `final_map_sites.csv` | Derived by this project from the sources above | this repository |

## Citations

### Ocean Networks Canada (continuous sites + Community Fishers casts)
Ocean Networks Canada Society (2026). *Oceans 3.0 Data Portal.*
https://data.oceannetworks.ca (accessed August 2026).

Sites used: Folger Deep/Pinnacle, Saanich Inlet (PVIP, SILL), Strait of Georgia
(SCVIP, SEVIP), Burrard Inlet (BIIP), Boundary Pass, Juan de Fuca moorings,
China Creek (CCIP), Barkley slope/canyon (NCBC, BACAX), and Community Fishers
stations. Oceans 3.0 issues per-deployment dataset DOIs - for a publication,
generate and cite the DOI for each instrument record used (Oceans 3.0 ->
dataset landing pages). Please acknowledge Ocean Networks Canada, a University
of Victoria initiative, as the data provider.

### Fisheries and Oceans Canada - Institute of Ocean Sciences (via CIOOS Pacific)
Fisheries and Oceans Canada, Institute of Ocean Sciences (2026). Datasets
`IOS_CTD_Profiles` (rosette CTD casts, 1965-present), `IOS_CTD_Moorings`
(moored CTD/oxygen time series), and `BCSOP_daily` (BC Shore Station
Oceanographic Program daily sea-surface temperature and salinity, 1914-present).
Distributed by the Canadian Integrated Ocean Observing System, Pacific region:
https://data.cioospacific.ca/erddap (accessed August 2026).
Licence: Open Government Licence - Canada.
Mooring fetch window extends west to 127 W to include shelf station E01
(49.29 N, 126.61 W), used as training data outside the mapped study box.

### ERA5 atmospheric reanalysis (Copernicus / ECMWF)
Hersbach, H., Bell, B., Berrisford, P., et al. (2020). The ERA5 global
reanalysis. *Quarterly Journal of the Royal Meteorological Society*, 146,
1999-2049. https://doi.org/10.1002/qj.3803

Hersbach, H., et al. (2023): *ERA5 hourly data on single levels from 1940 to
present.* Copernicus Climate Change Service (C3S) Climate Data Store (CDS).
https://doi.org/10.24381/cds.adbb2d47 (accessed August 2026; point time series
2006-2026 for six marine cells, including one off the Washington coast for
the OOI training sites).

Required acknowledgment: "Contains modified Copernicus Climate Change Service
information (2026). Neither the European Commission nor ECMWF is responsible
for any use of this information."

### NSF Ocean Observatories Initiative (Washington Coastal Endurance)
NSF Ocean Observatories Initiative (2026). Dissolved oxygen records from the
Coastal Endurance Array, Washington line: CE06ISSM, CE07SHSM, CE09OSSM
(surface moorings) and CE09OSPM (wire-following profiler). Data accessed
August 2026 from the OOI Data Explorer,
https://erddap.dataexplorer.oceanobservatories.org.
The OOI is funded by the U.S. National Science Foundation; please include the
OOI acknowledgment in publications.

### Environment and Climate Change Canada - Fraser River discharge
Environment and Climate Change Canada, National Hydrological Services /
Water Survey of Canada (2026). *Historical hydrometric data (HYDAT): daily
mean discharge, station 08MF005, Fraser River at Hope.* Accessed August 2026
via the MSC GeoMet OGC API, collection `hydrometric-daily-mean`,
https://api.weather.gc.ca. Record used: 2006-01-01 to 2025-02-24 (HYDAT is
the quality-controlled archive and lags the present).
Licence: Environment and Climate Change Canada Data Servers End-use Licence /
Open Government Licence - Canada.

### GEBCO bathymetry
GEBCO Compilation Group (2026). *GEBCO 2026 Grid* (15 arc-second global
terrain model). NERC EDS British Oceanographic Data Centre, NOC.
Subset used: 47.5-51.5 N, 130-122 W. **Copy the release DOI from your GEBCO
download page into this line before publication.**

### OceanHackWeek
This project was developed at OceanHackWeek 2026 (Bamfield Marine Sciences
Centre). Please acknowledge OceanHackWeek and the Bamfield Marine Sciences
Centre for hosting and infrastructure.

## Pending sources (cite only if added)

- NOAA Southwest Fisheries Science Center, Environmental Research Division
  (2026). Bakun coastal upwelling index, 48 N 125 W. NOAA CoastWatch/ERD
  ERDDAP.
- Vos, K.-based CE09OSPM processed profiler product (Zenodo,
  doi:10.5281/zenodo.15627742) - only if the research-grade profiler record
  replaces the ERDDAP stream.

## Units and processing note

All oxygen values were converted to mL/L at ingest
(1 mL/L = 1.429 mg/L; ~43.6 umol/kg at seawater density 1025 kg/m3).
Near-bottom cast values are the mean over the deepest 5 m of each profile;
continuous records are daily means. Full processing code is in this
repository's fetch and collection scripts.
