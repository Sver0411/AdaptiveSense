/*
 * policy_config.h — compile-time configuration -> runtime structures.
 *
 * The layer configuration types (`cd_config_t`, `as_config_t`) are plain
 * structs, which lets the host-side parity test build them too. This module is
 * the single place that maps the `CONFIG_AS_*` macros onto those structs, so
 * `main.c` on the device and `tests/c_host/parity_main.c` on the host are
 * guaranteed to run the identical policy configuration.
 *
 * It is deliberately free of any ESP-IDF dependency: `config.h` (provisioned
 * from `config.example.h`) only includes <stdbool.h> and <stdint.h>.
 *
 * No numeric literal may appear in this file except array indices — every value
 * comes from a macro that mirrors experiments/experiment_config.yaml.
 * scripts/check_config_parity.py enforces both halves of that statement.
 */
#ifndef ADAPTIVESENSE_POLICY_CONFIG_H
#define ADAPTIVESENSE_POLICY_CONFIG_H

#include "adaptive_scheduler.h"
#include "change_detector.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Fill the change-detector configuration from CONFIG_AS_* macros. */
void policy_build_detector_config(cd_config_t *out);

/* Fill the scheduler configuration from CONFIG_AS_* macros. */
void policy_build_scheduler_config(as_config_t *out);

#ifdef __cplusplus
}
#endif

#endif /* ADAPTIVESENSE_POLICY_CONFIG_H */
