# If no config.h exists yet (it is git-ignored), provision it from the example
# so the project builds out of the box. Real credentials later go into config.h
# only, which is never committed.
if(NOT EXISTS "${CMAKE_CURRENT_LIST_DIR}/config.h")
    message(STATUS "main/config.h missing - copying config.example.h -> config.h")
    execute_process(COMMAND ${CMAKE_COMMAND} -E copy
        "${CMAKE_CURRENT_LIST_DIR}/config.example.h"
        "${CMAKE_CURRENT_LIST_DIR}/config.h")
endif()

# Component build for the AdaptiveSense firmware.
#
# Component dependencies are declared with REQUIRES only. v0.1 also called
# require("mqtt") / require("wifi"), which are not ESP-IDF build-system commands
# (CMake aborts with "Unknown CMake command"), and separately declared a *managed*
# `mqtt` component in idf_component.yml while the sources include the built-in
# esp-mqtt. Two mechanisms for one dependency is exactly the ambiguity that made
# the firmware unbuildable; there is now exactly one.
idf_component_register(
    SRCS
        "main.c"
        "policy_config.c"
        "sensor.c"
        "sensor_supervisor.c"
        "bme280_math.c"
        "change_detector.c"
        "adaptive_scheduler.c"
        "communication.c"
        "communication_payload.c"
        "power_mgmt.c"
    INCLUDE_DIRS "."
    REQUIRES
        esp_wifi
        esp_event
        esp_netif
        nvs_flash
        mqtt
        driver
        esp_timer
        esp_pm
)
