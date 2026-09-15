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
idf_component_register(
    SRCS
        "main.c"
        "sensor.c"
        "change_detector.c"
        "adaptive_scheduler.c"
        "communication.c"
        "power_mgmt.c"
    INCLUDE_DIRS "."
)

# Security: Wi-Fi/MQTT credentials are NOT compiled in from committed sources.
# They live in the git-ignored main/config.h, initialised from config.example.h.
require("mqtt")
require("wifi")