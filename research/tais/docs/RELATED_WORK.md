# Initial related-work review

**Status:** focused initial scan, not a systematic review. The papers below constrain our claims, but more searches and full-text checks are required before asserting novelty.

## Closest work

### 1. Rao et al. (2019) — critical-sensing-aware adaptive data acquisition

Yuan Rao et al., “Adaptive Data Acquisition with Energy Efficiency and Critical-Sensing Guarantee for Wireless Sensor Networks,” *Sensors*, 19(12), 2654 (2019). DOI: [10.3390/s19122654](https://doi.org/10.3390/s19122654).

- Combines energy-saving compressed-sensing and prediction-based modes, with adaptive deviation tolerance and switching to improve capture of critical events.
- It is directly relevant to agricultural IoT and critical-event retention.
- The paper identifies outliers caused by sensor failure or lossy wireless links as future work. This motivates testing faults explicitly, but does **not** establish that no later paper has addressed them.
- Implication: “critical-event-aware adaptive sampling” is not our contribution by itself.

### 2. Masoum, Meratnia & Havinga (2012) — quality-aware adaptive sampling

“A Decentralized Quality Aware Adaptive Sampling Strategy in Wireless Sensor Networks,” IEEE UIC/ATC 2012, pp. 298–305. DOI: [10.1109/UIC-ATC.2012.156](https://doi.org/10.1109/UIC-ATC.2012.156).

- Adapts sampling based on environmental conditions, user-defined data ranges, and temporal/spatial correlation.
- Implication: threshold/range-aware sampling and quality-aware adaptation are established ideas. Threshold proximity is therefore an optional cue to evaluate, not a novelty claim on its own.

### 3. Harb et al. (2024) — CLARA fault tolerance and sampling adaptation

Hassan Harb et al., “CLARA: A cluster-based node correlation for sampling rate adaptation and fault tolerance in sensor networks,” *Internet of Things*, 28 (2024), 101345. DOI: [10.1016/j.iot.2024.101345](https://doi.org/10.1016/j.iot.2024.101345).

- Uses node correlation to adapt sampling rates and fault-tolerant reconstruction at the sink.
- Evaluates simulations and real sensor motes.
- Implication: combining sampling adaptation and fault tolerance is not new in general. Our possible distinction is narrower: channel-level trust gating with the existing SensorTrust detector, independent event/fault ground truth, and explicit evaluation of whether a fault on one channel suppresses events on another. This distinction needs further literature verification.

### 4. Lu et al. (2026) — sentinel-assisted adaptive sampling for anomaly monitoring

Guoqing Lu, Yixuan Sun, Yiwen Jiang, Bernard Butler, “Adaptive Sampling for Spatiotemporal Anomaly Monitoring in Wireless Sensor Networks,” arXiv:2607.15235 (16 July 2026). [arXiv page](https://arxiv.org/abs/2607.15235).

- Uses Kalman-filter prediction uncertainty for sparse sensing, continuously sampled sentinel nodes for anomaly detection, and neighborhood wake-up for localized events.
- It is a recent preprint and must be treated as such unless a peer-reviewed version is found.
- Implication: anomaly-aware sampling and event-window visibility are active research areas. Our first version does not attempt to compete with its spatial sentinel architecture; it studies a different node-level fault/health gating question.

## Real agricultural trace

### 5. Hull et al. (2023) — greenhouse temperature/humidity and actuator states

Keegan Hull et al., “Dataset of temperature, humidity, and actuator states of an east-facing South African Greenhouse Tunnel,” *Data in Brief*, 51 (2023), 109633. DOI: [10.1016/j.dib.2023.109633](https://doi.org/10.1016/j.dib.2023.109633).

- Dataset DOI: [10.17632/54htxm94bv.1](https://doi.org/10.17632/54htxm94bv.1), [Mendeley Data page](https://data.mendeley.com/datasets/54htxm94bv/1).
- Contains 162 days of temperature and humidity from three greenhouse positions, sampled/stored at 5-minute intervals, plus fan/wet-wall state. The middle sensor's control thresholds are reported as fan on above 30 °C and off below 22 °C.
- The Mendeley page lists the dataset as CC BY 4.0. Cite the dataset and paper and preserve attribution if derived files are redistributed.
- Limitation: it does not supply labelled sensor-fault episodes. It can test scheduling/reconstruction on a real signal and use actuator-state transitions as an explicitly labelled control-transition proxy; injected fault labels must be generated independently.

## What we can and cannot claim

**Can investigate**
- Whether trust gating changes the event-recall / false-alarm / upload-cost trade-off under controlled fault injection.
- Whether faulty-channel decisions remain local and healthy channels still contribute to node scheduling.
- Whether findings persist on a real greenhouse trace when fault injection is applied to a copy of the observations.

**Cannot claim yet**
- That the proposed method is the first of its kind.
- That the method beats CLARA, AAS, or the 2026 sentinel preprint without implementing or faithfully reproducing comparable baselines.
- That synthetic fault-injection results establish field reliability.
- That application-upload reduction equals radio energy savings.

## Search plan before publication-oriented claims

1. Search 2017–2026 literature for “sensor reliability-aware sampling”, “fault-aware adaptive sampling”, “event detection under sensor faults”, “sensor quality adaptive sampling”, and “fault-tolerant event-triggered sensing”.
2. Follow references and citations from AAS, CLARA, and the 2026 sentinel preprint.
3. Inspect full methods and experimental assumptions of the closest papers.
4. Add a comparison matrix listing sensing unit, fault model, event ground truth, communication model, baselines, and evaluation metrics.
5. Adjust the research question if a directly equivalent method is found.
