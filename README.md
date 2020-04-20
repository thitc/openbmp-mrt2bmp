OpenBMP MRT2BMP
===============

***
~~~

OpenBMP MRT2BMP code changes in this fork:

- Python 3
- Run as Docker container
- Config YAML contains only logging settings (other settings via Env Vars)
- Processed files are deleted (not moved)
- MRT filename pattern changed to match BIRD format
- RIPE and Route Views disabled
- Logging File Handler removed
- Config Filename hardcoded (contains only logging settings)
- Only 1 MRT router can export to this script. Support for multiple routers disabled (run as sidecar)
- MRT files must be stored in ROUTER_DATA_PATH w/o any subfolder; default path is /var/run/openbmp/router_data
- MRT prefix is rib ONLY; process only peer index table (is_first_run in processRouteView disabled)


Use Case of this fork:

- Run as sidecar container (Kubernetes) and process MRT files from BIRD Route Server
- Process only peer index table from RIB


! Code is modified to be used in temp showcase project and not production !

... work in progress

~~~
***

This consumer reads MRT files of a router and sends natively in BMP format to a remote collector continuously.

> When you exit MRT2BMP, **router** and **peers** will be shown **down**.

### MRT2BMP Structure

    Router --> MRT --> MRT2BMP --> OpenBMP Collector --> Kafka Message Bus --> MySQL Consumer

### Env Vars

> - `MRT_ROUTER` Hostname of MRT Router; mandatory
> - `COLLECTOR_FQDN` Collector FQDN; mandatory
> - `COLLECTOR_PORT` Collector Port; optional, default = 5000
> - `STARTUP_DELAY` Delay after init and peers up; optional, default = 5
> - `MAX_QUEUE_SIZE` Max size of messages in queue to be written; optional, default = 10000
> - `TIMESTAMP_INTERVAL_LIMIT` Max number of minutes between two consecutive mrt files; optional, default = 20
> - `IGNORE_TIMESTAMP_INTERVAL_ABNORM` If program will ignore file timestamp intervals; optional, default = True
> - `ROUTER_DATA_PATH` Master directory of the routers data; optional, default = /var/run/openbmp/router_data
> - `LOG_LEVEL` Log Level; optional, default = INFO

### MRT File format

> - `rib.2020-04-17.09:54:48.mrt`





















### Running:

Configure
-----------------------------------------
Default config file path is **src/etc/openbmp-mrt2bmp.yml**
> Change **collector address** and **collector port** in the config file.

1-) Running a router with your MRT files
-----------------------------------------

If you install the python code, then you should be able to run from a terminal

    nohup openbmp-mrt2bmp -c <configuration file> -r <router name> > /dev/null 2>&1 &

If you are running from within the **git** directory, you can run it as follows:

     nohup PYTHONPATH=./src/site-packages python src/bin/openbmp-mrt2bmp -c src/etc/openbmp-mrt2bmp.yml -r <router name> > /dev/null 2>&1 &

> **IMPORTANT**: Router directory structure must follow directory structure below.
You can find example router directory structure in **src/etc/example_routers**. "example_routers" directory is the example root directory in which router directories are.

> **Router Directory Structure Explaination**
> - **Root/Base directory:** Directory in which router directories are stored. Name of this directory must be the same as root/base directory name in config file.
> - **Router directory:** Directory in which router's subdirectories are stored. Name of this directory will be the router name.
> - **Subdirectory:** Subdirectories in which **RIBS** and **UPDATES** directories are stored. Name of these directories must be in format **YYYY.MM**. e.g. "2017.03"
> - **RIBS Directory:** Directory in which **RIB** files are stored. Name of this directory must be "RIBS".
<br> - File name of a **RIB** file must be in format **"rib.YYYY-MM-DD.HH:MM:SS"** or **"bview.YYYY-MM-DD.HH:MM:SS"**. e.g. **"rib.2020-04-17.09:54:48"**, **"bview.2020-04-17.09:54:48"**
> - **UPDATES Directory:** Directory in which **UPDATES** files are stored. Name of this directory must be "UPDATES".
<br> - File name of a **UPDATES** file must be in format **"updates.YYYY-MM-DD.HH:MM:SS"**. e.g. **"updates.2020-04-17.09:54:48"**

> **RIB** and **UPDATES** files can have **.gzip**, **.bz2** and **.gz** file format extensions in their file names. e.g. "rib.2020-04-17.09:54:48.gzip", "rib.2020-04-17.09:54:48.bz2", "updates.2020-04-17.09:54:48.gz"

### Router Directory Structure

    Root/base directory
        |
        |---- DIR: <router name>                            # e.g. "route-views2.oregon-ix.net","rrc00.ripe.net"
            |
            |---- DIR: <subdirectory name>                  # e.g. "2016.11"
                |
                |---- DIR: RIBS
                |    |
                |    |---- FILE: rib.20161128.0800.bz2      # Rib file
                     |---- FILE: bview.20170222.1600.gz      # Bview file
                |
                |---- DIR: UPDATES
                     |
                     |---- FILE: updates.20161128.0800.bz2  # Update file
                     |---- FILE: updates.20161128.0815.bz2  # Update file
                     |---- FILE: updates.20161128.0830.bz2  # Update file
                     |---- FILE: updates.20161128.0845.bz2  # Update file

- Compressed MRT files in **.gzip**, **.bz2** and **.gz** formats are supported.

2-) Running a router with MRT files from routeviews.org
-------------------------------------------------------

You can see list of routers from routeviews.org by running it as follows:

    openbmp-mrt2bmp --rv list

If you install the python code, then you should be able to run from a terminal

    nohup openbmp-mrt2bmp -c <configuration file> --rv <router name> > /dev/null 2>&1 &

#### Example Run:

    nohup openbmp-mrt2bmp -c src/etc/openbmp-mrt2bmp.yml --rv route-views2.oregon-ix.net > /dev/null 2>&1 &

If you are running from within the **git** directory, you can run it as follows:

    nohup PYTHONPATH=./src/site-packages python src/bin/openbmp-mrt2bmp -c src/etc/openbmp-mrt2bmp.yml --rv <router name> > /dev/null 2>&1 &

3-) Running a router with MRT files from ripe.net
-------------------------------------------------

You can see list of routers from ripe.net by running it as follows:

    openbmp-mrt2bmp --rp list

If you install the python code, then you should be able to run from a terminal

    nohup openbmp-mrt2bmp -c <configuration file> --rp <router name> > /dev/null 2>&1 &

#### Example Run:

    nohup openbmp-mrt2bmp -c src/etc/openbmp-mrt2bmp.yml --rp rrc00.ripe.net > /dev/null 2>&1 &

If you are running from within the **git** directory, you can run it as follows:

    nohup PYTHONPATH=./src/site-packages python src/bin/openbmp-mrt2bmp -c src/etc/openbmp-mrt2bmp.yml --rp <router name> > /dev/null 2>&1 &

#### Usage
```
Usage: ./openbmp-mrt2bmp [OPTIONS]

OPTIONS:
  -h, --help                        Print this help menu
  -c, --config                      Config filename (default is src/etc/openbmp-mrt2bmp.yml)
  -r, --router                      Router name which you want to run with your MRT files
  --rv, --routeviews                Router name which you want to run from routeviews.org
  --rv list, --routeviews list      Print name of routers from routeviews.org
  --rp, --ripe                      Router name which you want to run from ripe.net
  --rp list, --ripe list            Print name of routers from ripe.net
```

#### Configuration
Configuration is in YAML format via the **openbmp-mrt2bmp.yml** file.  See the file for details.

> ** You should provide **directory paths** that are **writable** by the consumer.

