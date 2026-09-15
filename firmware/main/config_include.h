/*
 * config_include.h — include the device configuration.
 *
 * On the device, CMake provisions the git-ignored `config.h` from the committed
 * `config.example.h`. In a host-side test build there is no `config.h`, so this
 * header falls back to the example — which is the same file the device is built
 * from by default, so a host test validates the real configuration and not a
 * copy of it.
 *
 * Every translation unit that needs the configuration includes this header
 * rather than `config.h`, so the fallback exists in exactly one place.
 */
#ifndef ADAPTIVESENSE_CONFIG_INCLUDE_H
#define ADAPTIVESENSE_CONFIG_INCLUDE_H

#if defined(__has_include)
#  if __has_include("config.h")
#    include "config.h"
#  else
#    include "config.example.h"
#  endif
#else
#  include "config.h"
#endif

#endif /* ADAPTIVESENSE_CONFIG_INCLUDE_H */
