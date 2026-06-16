# Insuretek Insurance Semantic Analysis Rules (ISAR)

ISAR-VERSION: 2026-06-15.1
ISAR-EFFECTIVE-DATE: 2026-06-15

Reference rules for interpreting raw applicant, policy, and risk data when answering questions about an insurance applicant. Apply these definitions to the raw facts supplied — do not restate this block itself in the output.

---

## Policy Types and Coverage Scope

| Policy Type | Primary Risk | Key Underwriting Factors | Typical Deductibles | Coverage Limits |
|---|---|---|---|---|
| **HOME** | Residential property damage, liability, personal property | Property age, construction, location, prior claims, square footage | $500–$2,500 | $100k–$500k |
| **AUTO** | Vehicle liability, collision, comprehensive | Driver history, vehicle type, annual mileage, usage pattern, claims history | $250–$1,000 | $50k–$300k |
| **COMMERCIAL** | Business property, liability, workers comp | Business type, annual revenue, employee count, loss history, property value | $1,000–$5,000 | $500k–$5M |
| **LIFE** | Death benefit, income replacement, estate planning | Age, health status, occupation, medical history, coverage amount | N/A (lump sum) | $100k–$2M |

---

## Risk Tier Classification

Risk tiers determine pricing, coverage limits, and underwriting requirements. Apply in order — first match wins.

### DECLINED (Ineligible for coverage)
- Prior claim activity exceeds tolerance (`prior_claims_count`: HOME: >3 claims/5yrs, AUTO: >2 claims/3yrs, COMMERCIAL: major loss/recent)
- Property condition critical issues (`occupancy_type` = vacant, `property_type` unsuitable; uninhabitable, severe damage, high-risk occupancy)
- Applicant background disqualifying factors (`applicant_age` >80 for LIFE, hazardous occupation, fraud indicators)
- Coverage request unreasonable (`coverage_amount` vs. `declared_value` mismatch; >5x or suspicious)
- Age/health profile incompatible with policy type (`applicant_age` >80 for LIFE, `medical_history` terminal diagnosis)

### HIGH_RISK (Restricted coverage, elevated premium)
- Prior claims present but within tolerance (`prior_claims_count`: 1–2 claims in lookback period)
- Property characteristics elevated risk (`year_built` <1950, `construction_type` mixed, `state_or_province_code` high-hazard area)
- Occupancy concerns (`occupancy_type` tenant_occupied or seasonal, unusual use)
- Applicant profile flags (`years_in_business` <1 year, `credit_score` low, economic volatility)
- Coverage mismatch (`coverage_amount` >5x `declared_value` or below-market request)

### STANDARD (Standard rates, normal conditions)
- No prior claims or isolated claim 5+ years ago (`prior_claims_count` = 0 or resolved)
- Standard property characteristics (`year_built` 1950–2000, `construction_type` typical, `occupancy_type` owner_occupied)
- Applicant profile stable (`years_in_business` 1–3 years, established household/business)
- Coverage reasonable relative to property value (`coverage_amount` aligns with `declared_value`)
- No exceptional risk factors

### PREFERRED (Best rates, excellent profile)
- Zero claims in lookback period (`prior_claims_count` = 0)
- Property characteristics below-average risk (`year_built` >2000, `construction_type` masonry/steel, low-hazard `state_or_province_code`)
- Applicant actively risk-mitigating (`safety_features` present, loss prevention systems)
- Coverage conservative relative to property value (`coverage_amount` <1.5x `declared_value`)
- Exceptional profile (`years_in_business` >3, excellent history, strong financials, proactive management)

---

## Policy Lifecycle and Underwriting Phases

| Status | Meaning | Underwriting Action | Next Phase |
|---|---|---|---|
| **DRAFT** | Intake submitted; awaiting initial assessment | Validate applicant data completeness, assess risk indicators | QUOTED |
| **QUOTED** | Risk assessment complete; premium estimate provided | Applicant reviews quote, decides to proceed or abandon | BOUND or abandoned |
| **BOUND** | Applicant accepts coverage terms; waiting for payment | Issue policy documents, activate coverage, collect premium | ACTIVE |
| **ACTIVE** | Policy live and claims-eligible | Monitor claims, manage renewals, handle requests | ACTIVE (ongoing) or CANCELLED |
| **CANCELLED** | Applicant or Insuretek terminated; no claims eligibility | Process final settlement, archive records | Terminal |
| **EXPIRED** | Coverage period ended naturally; policy lapsed | Renewal solicitation, archive if not renewed | Terminal or renew to ACTIVE |

---

## Home Insurance Underwriting Rules

### Property Risk Factors (HIGHEST PRIORITY)
1. **Year Built** (`year_built`) — Older properties = higher risk
   - Pre-1950: HIGH_RISK baseline; additional inspection required
   - 1950–1980: Standard tier baseline
   - Post-2000: Preferred baseline if good condition
2. **Construction Type** (`construction_type`) — Material determines durability
   - Masonry/concrete/steel: Lower risk (PREFERRED eligible)
   - Wood frame: Standard risk (STANDARD baseline)
   - Mixed/unconventional: HIGH_RISK flag
3. **Roof Condition** — Major claim vector (inferred from `year_built` and `construction_type`)
   - Asphalt shingle <20 yrs old: Standard
   - Asphalt shingle >30 yrs old: HIGH_RISK, may require replacement
   - Metal/tile: Preferred baseline
   - Flat roof: HIGH_RISK flag
4. **Square Footage** (`square_footage`) — Larger properties = higher exposure
   - <2,000 sq ft: Standard baseline
   - 2,000–4,000 sq ft: Standard with modest deductible increase
   - >4,000 sq ft: HIGH_RISK unless exceptional condition
5. **Occupancy Type** (`occupancy_type`) — Owner-occupied is preferred
   - owner_occupied: Standard baseline
   - tenant_occupied: HIGH_RISK; additional underwriting
   - vacant (>30 days): DECLINED (uninsurable)

### Claims History (5-Year Lookback)
- **Prior Claims Count** (`prior_claims_count`) — 5-year claims history
  - Zero claims: Preferred eligibility (+5–10% premium discount)
  - One claim: Standard tier; review cause
    - Water damage, weather: Acceptable
    - Theft/break-in: Flag for security assessment
  - Two or more claims: HIGH_RISK tier minimum
  - Three or more claims: DECLINED

### Location and Hazard Risk
- **Wildfire Zone**: HIGH_RISK; elevated deductible ($2,500+) required
- **Flood Zone**: Separate flood policy required; homeowners policy excludes flood
- **Hurricane Zone**: HIGH_RISK; reinforced roof mandatory
- **Urban Area**: Standard; theft/liability risk managed
- **Rural Area**: Standard unless isolation complicates emergency response

---

## Auto Insurance Underwriting Rules

### Driver Profile (HIGHEST PRIORITY)
1. **Age** (`driver_age`) — Younger/older drivers = higher risk
   - 16–25: HIGH_RISK baseline (inexperience + accident propensity)
   - 26–60: Standard baseline
   - 61–75: Standard with cautionary review
   - >75: HIGH_RISK; medical certification may be required
2. **Driving Record** (`driving_record`) — Violations and accidents determine tier
   - Clean (0 violations, 0 accidents): Preferred baseline
   - Minor violations (1–2 speeding): Standard tier
   - Major violations (DUI, reckless, suspended license): DECLINED or HIGH_RISK with restrictions
   - Multiple accidents: HIGH_RISK tier; rate increase significant
3. **Years Licensed** (`years_licensed`) — New drivers = higher risk
   - <2 years: HIGH_RISK baseline
   - 2–10 years: Standard baseline
   - >10 years: Preferred eligible if clean record

### Vehicle Characteristics
1. **Vehicle Type** (`vehicle_type`)
   - Sedan/SUV: Standard baseline
   - Sports car: HIGH_RISK baseline (higher accident/theft propensity)
   - Commercial vehicle: Special underwriting required
   - Motorcycle: DECLINED (outside scope or separate policy)
2. **Vehicle Age** (`vehicle_year`)
   - <5 years: Standard baseline
   - 5–10 years: Standard with modest adjustment
   - >10 years: Standard; collision/comprehensive coverage assessment
3. **Annual Mileage** (`annual_mileage`)
   - <15,000 miles: Preferred baseline (lower exposure)
   - 15,000–25,000 miles: Standard baseline
   - >25,000 miles: HIGH_RISK; commute/commercial use flag
4. **Safety Features** (inferred from `vehicle_year` and `vehicle_type`)
   - Modern anti-theft, ABS, airbags: Standard baseline
   - Older vehicles (pre-2000) without modern safety: HIGH_RISK flag

### Usage Pattern (`usage_type`)
- **Commute** (<50 miles/day): Standard baseline
- **Regional** (50–100 miles/day): Standard with modest premium increase
- **Commercial** (delivery, rideshare): DECLINED or special policy required
- **Pleasure only** (<5,000 miles/year): Preferred baseline with discount

### Claims History (3-Year Lookback)
- **Prior Claims Count** (`prior_claims_count`) — 3-year auto claims history
  - Zero claims: Preferred baseline (5–10% discount)
  - One accident: Standard tier; review fault determination
  - One violation: Standard tier; review severity
  - Multiple claims/violations: HIGH_RISK; rate increase significant
  - Two or more accidents within 3 years: HIGH_RISK tier minimum

---

## Commercial Insurance Underwriting Rules

### Business Profile (HIGHEST PRIORITY)
1. **Business Type & Industry** (`business_type` or `industry`) — Risk varies by industry exposure
   - Low-risk (professional services, tech, education): Standard baseline
   - Moderate-risk (retail, hospitality, healthcare): HIGH_RISK baseline
   - High-risk (manufacturing, contractors, hazardous materials): DECLINED or special underwriting
2. **Annual Revenue** (`annual_revenue`) — Scales coverage needs and exposure
   - <$500k: SMB tier; simple underwriting
   - $500k–$5M: Mid-market; standard commercial underwriting
   - >$5M: Enterprise; extensive loss history review required
3. **Years in Business** (`years_in_business`) — Established businesses = lower risk
   - <1 year: HIGH_RISK baseline
   - 1–3 years: Standard baseline
   - >3 years: Preferred eligible if stable
4. **Employee Count** (`employee_count`) — More employees = larger liability exposure
   - 1–5 employees: Standard baseline
   - 5–25 employees: Standard with enhanced liability limits
   - >25 employees: HIGH_RISK; workers comp and safety protocols mandatory

### Property Valuation
- **Building Value** (`declared_value` or `building_value`) — Replacement cost vs. claimed value
  - Value overestimated by >20%: DECLINED (fraud indicator)
  - Value reasonable relative to comparables: Standard
- **Contents Value** (`contents_value`) — Business equipment and inventory
  - <$100k: Standard baseline
  - $100k–$500k: Standard with inventory verification
  - >$500k: HIGH_RISK; independent appraisal required
- **Business Interruption** (`business_interruption_months`) — Income protection during closure
  - Requested >12 months expenses: HIGH_RISK flag
  - <12 months: Standard baseline

### Loss History (3-Year Lookback)
- **Prior Claims Count** (`prior_claims_count`) — 3-year commercial claims history
  - Zero claims: Preferred baseline
  - One claim <$50k: Standard tier; review cause
  - One major claim ($50k–$500k): HIGH_RISK tier
  - Multiple claims or combined >$500k: DECLINED or special underwriting
  - Catastrophic loss (>$1M): DECLINED

---

## Life Insurance Underwriting Rules

### Applicant Health Profile (HIGHEST PRIORITY)
1. **Age at Issue** (`applicant_age`)
   - 18–40: Preferred baseline (lowest mortality risk)
   - 41–60: Standard baseline
   - 61–75: HIGH_RISK baseline; medical exam required
   - >75: DECLINED (life expectancy concerns)
2. **Medical History** (`medical_history` or `health_conditions`) — Chronic conditions impact mortality
   - No conditions: Preferred baseline
   - Managed chronic condition (diabetes, hypertension, controlled): Standard tier; underwriting depends on control metrics
   - Serious condition (cancer, heart disease, COPD): HIGH_RISK; medical exam and specialist reports required
   - Terminal diagnosis: DECLINED
3. **Lifestyle Factors** (`smoker_status`, `occupation_hazard`)
   - Non-smoker: Standard baseline (or Preferred)
   - Smoker: HIGH_RISK; significant rate increase
   - Hazardous occupation (pilot, skydiver, miner): DECLINED or exclusion rider required
   - Substance abuse history: DECLINED
4. **Family Medical History** (`family_medical_history`)
   - Longevity (parents >80): Preferred indicator
   - Early mortality (parents <60): HIGH_RISK flag; medical exam
   - Genetic risk factors (early cancer, heart disease): HIGH_RISK; specialist exam required

### Coverage Request Assessment
- **Coverage Amount Relative to Income** (`coverage_amount` vs. `annual_income`)
  - <5x annual income: Standard baseline
  - 5–10x annual income: Standard; verify income adequately
  - >10x annual income: HIGH_RISK flag; potential overinsurance or moral hazard
- **Term Length** (`term_years`)
  - 10–20 years: Standard baseline
  - 20–30 years: Standard; applicant age must support term
  - >30 years: Applicant age must be <40; otherwise HIGH_RISK

---

## Premium Estimation Factors

Apply in priority order for coverage amount and deductible calculation:

1. **Risk Tier** (`risk_tier`) (HIGHEST IMPACT)
   - PREFERRED: Base rate –15% to –10%
   - STANDARD: Base rate (100%)
   - HIGH_RISK: Base rate +25% to +50%
   - DECLINED: Not applicable (coverage denied)

2. **Policy Type & Coverage Limits** (`policy_type`, `coverage_amount`)
   - HOME: $100k limit base; +1–2% per $10k increase
   - AUTO: $100k bodily injury limit base; +1–3% per 25% increase
   - COMMERCIAL: $250k property base; varies by industry (+2–5% per $100k)
   - LIFE: $100k term base; +0.5–1% per $100k increase

3. **Deductible Selection** (`deductible`)
   - Higher deductible = lower premium (inverse relationship, typically –5–15%)
   - $500 deductible: +15% premium (lower cost barrier)
   - $2,500 deductible: –10% premium
   - $5,000+ deductible: –20%+ premium (self-insure small claims)

4. **Location** (`state_or_province_code`, hazard zone flags) (HOME/AUTO)
   - Urban (normal): Base rate
   - Coastal/Hazard Zone: +10–25% (HOME)
   - Rural: –5% (less claims density)

5. **Claims History** (`prior_claims_count`) (All lines)
   - Zero claims: –5–10% (preferred history discount)
   - One claim: 0% (baseline)
   - Each additional claim: +15–25% per claim

---

## Underwriting Gap Analysis

Common gaps between applicant data and underwriting requirements:

| Policy Type | Critical Gap | Impact | Remediation |
|---|---|---|---|
| HOME | Roof age/condition missing | Cannot underwrite; age >30yrs HIGH_RISK | Require roof inspection report |
| HOME | Square footage missing | Cannot estimate property value | Require property appraisal or tax record |
| AUTO | Driving record unavailable | Cannot assess risk | Require motor vehicle record from DMV |
| AUTO | Vehicle VIN missing | Cannot assess safety/loss history | Require complete VIN |
| COMMERCIAL | Loss history missing | Cannot underwrite | Require loss runs from prior carriers |
| COMMERCIAL | Building appraisal missing (>$500k) | Cannot verify value; fraud risk | Require professional appraisal |
| LIFE | Medical exam missing (age >50) | Cannot assess mortality | Schedule APS (attending physician statement) |
| LIFE | Hazardous occupation unclear | Cannot classify correctly | Require occupation clarification |

---

## Applicant Type Expectations

| Type | Typical Coverage | Underwriting Complexity | Decision Timeline |
|---|---|---|---|
| **SMB** (small business/household) | Standard limits, basic coverage | Simple (1–2 key factors) | 24–48 hours |
| **Mid-Market** (growing business/affluent household) | Enhanced limits, add-on coverage (umbrella, etc.) | Moderate (3–5 factors, verification) | 2–5 days |
| **Enterprise** (large business/high-net-worth) | Bespoke limits, specialized coverage | Complex (full underwriting, loss control, medical review) | 5–10 days |
| **Aggregator** (group/association) | Negotiated group rates, blanket coverage | Negotiated; depends on member composition | Varies by agreement |

---

## Field Interpretation Reference: Domain to Business Terminology

When reading applicant data, map technical field names to business underwriting concepts:

### Applicant Profile Fields

| Domain Field | Business Terminology | Business Meaning | Underwriting Relevance |
|---|---|---|---|
| `applicant_name` | Applicant / Business Name | Legal name of individual or entity seeking coverage | Identity verification; required for all underwriting |
| `policy_type` | Coverage Type | Type of insurance requested: HOME, AUTO, COMMERCIAL, LIFE | Determines underwriting rules, risk factors, premium base |
| `declared_value` | Property/Asset Value (as Declared by Applicant) | Total value of property/asset applicant is insuring | Primary determinant of coverage limits; flags if grossly overstated (fraud risk) or understated |

### Property & Risk Assessment Fields (`risk_profile`)

| Domain Field | Business Terminology | Business Meaning | Underwriting Relevance |
|---|---|---|---|
| `property_type` | Structure Classification | Type of structure (e.g., single_family, condo, commercial) | Determines base risk and coverage scope; condo vs. single-family = different liability |
| `year_built` | Age of Property | Year property was constructed | Critical risk factor; <1950 = HIGH_RISK (outdated materials, systems); >2000 = preferred |
| `prior_claims_count` | Claims History (5-Year Lookback) | Number of insurance claims filed in past 5 years | Primary indicator of risk; >2 claims = HIGH_RISK; >3 = DECLINED |
| `construction_type` | Building Material | Primary construction material (frame, masonry, steel, mixed) | Durability & fire risk; masonry/steel = preferred; wood frame = standard; mixed = HIGH_RISK |
| `occupancy_type` | Occupancy Status | How property is used (owner_occupied, tenant_occupied, vacant) | Owner-occupied = preferred; tenant = HIGH_RISK; vacant = DECLINED (no maintenance, liability) |
| `square_footage` | Insured Area | Total area in square feet | Larger area = higher exposure; >4,000 sq ft triggers premium adjustment |

### Coverage & Cost Fields (`coverage_request`)

| Domain Field | Business Terminology | Business Meaning | Underwriting Relevance |
|---|---|---|---|
| `coverage_amount` | Requested Coverage Limit | Dollar amount of coverage applicant is requesting | Must align with declared_value; if requesting >5x value = overinsurance flag (moral hazard); if <1x value = underinsurance gap |
| `deductible` | Deductible / Self-Insure Amount | Amount applicant will pay out-of-pocket for claims | Higher deductible = lower premium (inverse relationship); typical range $500–$5,000 for HOME |

### Location Risk Fields (`location_address`)

| Domain Field | Business Terminology | Business Meaning | Underwriting Relevance |
|---|---|---|---|
| `line1` | Street Address | Property street address | Required for hazard zone lookup (wildfire, flood, hurricane); enables ZIP code risk assessment |
| `city` | City / Municipality | City where property is located | Enables local loss history lookup; high-loss areas trigger premium increase |
| `state_or_province_code` | State / Province | State/province code (TX, CA, etc.) | Determines regulatory environment, local loss experience, catastrophic risk exposure |
| `postal_code` | ZIP Code | ZIP code for precise location | Enables granular hazard zone determination (flood, wildfire, coastal); premium adjustments |
| `country_code` | Country | Country code (default: US) | Non-US properties may trigger different underwriting or exclusion |

### Applicant Contact Fields (`contact_info`)

| Domain Field | Business Terminology | Business Meaning | Underwriting Relevance |
|---|---|---|---|
| `email` | Email Address | Applicant's email for communication | Required for policy delivery, claims communication; email patterns may indicate fraud |
| `phone` | Phone Number | Applicant's phone number | Required for underwriting verification; enables loss prevention outreach |
| `phone_country_code` | Phone Country Code | Country code for international numbers | Supports international applicants; flags if phone doesn't match property location |

### Issued Policy Fields (`PolicyData`)

| Domain Field | Business Terminology | Business Meaning | Underwriting Relevance |
|---|---|---|---|
| `applicant_id` | Applicant Reference | Unique identifier linking policy to applicant record | Used to retrieve applicant profile; enables cross-session history |
| `policy_type` | Coverage Type Issued | Type of policy issued (HOME, AUTO, COMMERCIAL, LIFE) | Determines claim eligibility, coverage rules, renewal process |
| `coverage_amount` | **ACTUAL** Coverage Limit (Issued) | Dollar amount of coverage Insuretek issued (may differ from requested) | Underwriting decision may reduce requested coverage if overinsurance detected |
| `deductible` | **ACTUAL** Deductible (Issued) | Deductible amount in issued policy (may differ from requested) | Underwriting may increase deductible if high-risk profile |
| `premium_estimate` | Quoted Annual Premium | Annual premium cost to applicant | Quote amount; premiums typically adjusted after binding based on final verification |
| `status` | **Policy Lifecycle Stage** | Current stage of policy (see Policy Status Lifecycle above) | Determines if policy is active (claims-eligible) or in pre-binding stage |

### Enum Values (from `src/core_data/domain/enums.py`)

#### PolicyType Enum
- HOME
- AUTO
- COMMERCIAL
- LIFE

#### RiskTier Enum
- STANDARD
- PREFERRED
- HIGH_RISK
- DECLINED

#### PolicyStatus Enum
- DRAFT (intake submitted; awaiting assessment)
- QUOTED (risk assessment complete; premium provided)
- BOUND (applicant accepted; awaiting payment)
- ACTIVE (policy live; claims-eligible)
- CANCELLED (coverage terminated)
- EXPIRED (coverage period ended naturally)

#### ApplicantType Enum
- SMB (small business or household)
- MID_MARKET (Mid-Market)
- ENTERPRISE (enterprise)
- AGGREGATOR (aggregator/group)

### Risk Tier Classification Algorithm

**Evaluation Order: DECLINED → HIGH_RISK → PREFERRED → STANDARD (first match wins)**

1. **Evaluate DECLINED criteria first** (Any single match = Immediate Decline):
   - HOME: `prior_claims_count` >= 3 OR `occupancy_type` == "vacant"
   - AUTO: `prior_claims_count` >= 2 OR `driving_record` contains ["DUI", "reckless", "suspended"]
   - COMMERCIAL: `prior_claims_count` > 1 with single loss > $500k OR `business_type` in ["hazardous_materials", "high_risk_industry"]
   - LIFE: `applicant_age` > 75 OR `medical_history` == "terminal_diagnosis"

2. **Evaluate HIGH_RISK factors** (First match triggers HIGH_RISK tier):
   - HOME: `year_built` < 1950 OR `construction_type` in ["mixed", "unconventional"] OR `square_footage` > 4000 OR `occupancy_type` == "tenant_occupied"
   - AUTO: `driver_age` in [16-25, >75] OR `years_licensed` < 2 OR `vehicle_type` == "sports_car" OR `prior_claims_count` >= 1
   - COMMERCIAL: `years_in_business` < 1 OR `employee_count` > 25 OR `business_type` in ["moderate_risk_industry"]
   - LIFE: `applicant_age` in [61-75] OR `medical_history` contains ["serious_condition", "managed_chronic"]

3. **Evaluate PREFERRED criteria** (ALL conditions must be met simultaneously):
   - `prior_claims_count` == 0
   - HOME: `year_built` > 2000 AND `construction_type` in ["masonry", "steel"] AND `occupancy_type` == "owner_occupied" AND `square_footage` <= 4000
   - AUTO: `driving_record` == "clean" AND `years_licensed` > 10 AND `annual_mileage` < 15000 AND `driver_age` in [26-60]
   - COMMERCIAL: `years_in_business` > 3 AND `employee_count` <= 25 AND `business_type` in ["low_risk_industry"] AND stable financials
   - LIFE: `applicant_age` in [18-40] AND `medical_history` == "no_conditions" AND `smoker_status` == "non_smoker"

4. **Default to STANDARD** if no PREFERRED or HIGH_RISK factors apply

---

Evaluate all sections above before responding.
