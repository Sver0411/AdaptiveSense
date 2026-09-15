cmake_minimum_required(VERSION 3.16)

# AdaptiveSense is built with the ESP-IDF build system.
# Configure the IDF before building, e.g.:
#   . $IDF_PATH/export.sh
#   idf.py set-target esp32s3
#   idf.py build
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(AdaptiveSense)