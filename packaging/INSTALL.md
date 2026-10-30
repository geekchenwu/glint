# Installing Lumen

Supported desktop: GNOME Shell 50 on Debian/Ubuntu with GTK 4.14 or newer.

## Recommended installer

Run the versioned installer as your normal desktop user:

```sh
sh Lumen-1.4.0.run
```

It contains the Debian package, verifies its checksum, and asks for administrator
authentication through the desktop (or sudo in a terminal). APT installs required
dependencies, including `python3-gi-cairo`, before Lumen starts. Internet access
and working APT repositories are needed for dependencies that are not cached.

`--check` previews APT's installation plan without installing anything.
`--extract DIRECTORY` extracts the embedded Debian package.

## Debian package

The `.deb` declares the same complete dependency list. Use a package manager that
resolves dependencies:

```sh
sudo apt install ./lumen_1.4.0_all.deb
lumen activate
```

Opening Lumen from the application menu also activates it and starts a capture.
`dpkg -i` alone does not download missing dependencies. Package maintainer scripts
do not invoke APT recursively.

## First session

There is no need to log out before taking the first screenshot. If GNOME has not
discovered the new extension yet, Lumen uses the system screenshot portal.
Approve the system screenshot request on first use. Selection, annotation, copy,
save, and basic clipboard pinning work in this mode.

Unoccupied F1, Ctrl+F1, F3, and Shift+F3 shortcuts are registered for this session.
Existing custom shortcuts are preserved, and Lumen offers to back up and take
over conflicts. `lumen hotkeys restore` restores that backup.

The native top bar indicator, window/menu detection, capturing open menus, and
always-on-top pins become available at the next normal login. Lumen then removes
its temporary shortcuts. Manually disabled extensions stay disabled.

An older editor process keeps its clipboard and pins during an upgrade. If a
command requires a new editor feature, save its pins, exit the older editor process,
and launch Lumen again. Restarting the editor does not require logging out.
