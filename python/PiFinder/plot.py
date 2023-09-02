#!/usr/bin/python
# -*- coding:utf-8 -*-
"""
This module handles plotting starfields
and constelleations
"""
import os
import io
import datetime
import numpy as np
import pandas
import time
from pathlib import Path
from PiFinder import utils
from PIL import Image, ImageDraw, ImageFont, ImageChops, ImageOps

from skyfield.api import Star, load, utc, Angle
from skyfield.constants import GM_SUN_Pitjeva_2005_km3_s2 as GM_SUN
from skyfield.data import hipparcos, mpc, stellarium
from skyfield.projections import build_stereographic_projection
from PiFinder.integrator import sf_utils


class Starfield:
    """
    Plots a starfield at the
    specified RA/DEC + roll
    """

    def __init__(self, colors, mag_limit=7, fov=10.2):
        self.colors = colors
        utctime = datetime.datetime(2023, 1, 1, 2, 0, 0).replace(tzinfo=utc)
        ts = sf_utils.ts
        self.t = ts.from_datetime(utctime)
        # An ephemeris from the JPL provides Sun and Earth positions.

        self.earth = sf_utils.earth.at(self.t)

        # The Hipparcos mission provides our star catalog.
        hip_path = Path(utils.astro_data_dir, "hip_main.dat")
        with load.open(str(hip_path)) as f:
            self.raw_stars = hipparcos.load_dataframe(f)

        # Image size stuff
        self.target_size = 128
        self.diag_mult = 1.422
        self.render_size = (
            int(self.target_size * self.diag_mult),
            int(self.target_size * self.diag_mult),
        )
        self.render_center = (
            int(self.render_size[0] / 2),
            int(self.render_size[1] / 2),
        )
        self.render_crop = [
            int((self.render_size[0] - self.target_size) / 2),
            int((self.render_size[1] - self.target_size) / 2),
            int((self.render_size[0] - self.target_size) / 2) + self.target_size,
            int((self.render_size[1] - self.target_size) / 2) + self.target_size,
        ]

        self.set_mag_limit(mag_limit)
        # Prefilter here for mag 9, just to make sure we have enough
        # for any plot.  Actual mag limit is enforced at plot time.
        bright_stars = self.raw_stars.magnitude <= 7.5
        self.stars = self.raw_stars[bright_stars].copy()

        self.star_positions = self.earth.observe(Star.from_dataframe(self.stars))
        self.set_fov(fov)

        # constellations
        const_path = Path(utils.astro_data_dir, "constellationship.fab")
        with load.open(str(const_path)) as f:
            self.constellations = stellarium.parse_constellations(f)
        edges = [edge for name, edges in self.constellations for edge in edges]
        const_start_stars = [star1 for star1, star2 in edges]
        const_end_stars = [star2 for star1, star2 in edges]

        # Start the main dataframe to hold edge info (start + end stars)
        self.const_edges_df = self.stars.loc[const_start_stars]

        # We need position lists for both start/end
        self.const_start_star_positions = self.earth.observe(
            Star.from_dataframe(self.const_edges_df)
        )
        self.const_end_star_positions = self.earth.observe(
            Star.from_dataframe(self.stars.loc[const_end_stars])
        )

        marker_path = Path(utils.pifinder_dir, "markers")
        pointer_image_path = Path(marker_path, "pointer.png")
        _pointer_image = Image.open(str(pointer_image_path)).crop(
            [
                int((256 - self.render_size[0]) / 2),
                int((256 - self.render_size[1]) / 2),
                int((256 - self.render_size[0]) / 2) + self.render_size[0],
                int((256 - self.render_size[1]) / 2) + self.render_size[1],
            ]
        )
        self.pointer_image = ImageChops.multiply(
            _pointer_image,
            Image.new("RGB", self.render_size, colors.get(64)),
        )
        # load markers...
        self.markers = {}
        for filename in os.listdir(marker_path):
            if filename.startswith("mrk_"):
                marker_code = filename[4:-4]
                _image = Image.new("RGB", self.render_size)
                _image.paste(
                    Image.open(f"{marker_path}/mrk_{marker_code}.png"),
                    (self.render_center[0] - 11, self.render_center[1] - 11),
                )
                self.markers[marker_code] = ImageChops.multiply(
                    _image, Image.new("RGB", self.render_size, colors.get(256))
                )

    def set_mag_limit(self, mag_limit):
        self.mag_limit = mag_limit

    def set_fov(self, fov):
        self.fov = fov
        angle = np.pi - (self.fov) / 360.0 * np.pi
        limit = np.sin(angle) / (1.0 - np.cos(angle))

        self.image_scale = int(self.target_size / limit)
        self.pixel_scale = self.image_scale / 2

        # figure out magnitude limit for fov
        mag_range = (7.5, 5)
        fov_range = (5, 40)
        perc_fov = (fov - fov_range[0]) / (fov_range[1] - fov_range[0])
        if perc_fov > 1:
            perc_fov = 1
        if perc_fov < 0:
            perc_fov = 0

        mag_setting = mag_range[0] - ((mag_range[0] - mag_range[1]) * perc_fov)
        self.set_mag_limit(mag_setting)

    def plot_markers(self, ra, dec, roll, marker_list):
        """
        Returns an image to add to another image
        Marker list should be a list of
        (RA_Hours/DEC_degrees, symbol) tuples
        """

        markers = pandas.DataFrame(
            marker_list, columns=["ra_hours", "dec_degrees", "symbol"]
        )

        # required, use the same epoch as stars
        markers["epoch_year"] = 1991.25
        marker_positions = self.earth.observe(Star.from_dataframe(markers))

        markers["x"], markers["y"] = self.projection(marker_positions)

        ret_image = Image.new("RGB", self.render_size)
        idraw = ImageDraw.Draw(ret_image)

        markers_x = list(markers["x"])
        markers_y = list(markers["y"])
        markers_symbol = list(markers["symbol"])

        ret_list = []
        for i, x in enumerate(markers_x):
            x_pos = int(x * self.pixel_scale + self.render_center[0])
            y_pos = int(markers_y[i] * -1 * self.pixel_scale + self.render_center[1])
            symbol = markers_symbol[i]

            if symbol == "target":
                idraw.line(
                    [x_pos, y_pos - 5, x_pos, y_pos + 5],
                    fill=self.colors.get(255),
                )
                idraw.line(
                    [x_pos - 5, y_pos, x_pos + 5, y_pos],
                    fill=self.colors.get(255),
                )

                # Draw pointer....
                # if not within screen
                if (
                    x_pos > self.render_crop[2]
                    or x_pos < self.render_crop[0]
                    or y_pos > self.render_crop[3]
                    or y_pos < self.render_crop[1]
                ):
                    # calc degrees to target....
                    deg_to_target = (
                        np.rad2deg(
                            np.arctan2(
                                y_pos - self.render_center[1],
                                x_pos - self.render_center[0],
                            )
                        )
                        + 180
                    )
                    tmp_pointer = self.pointer_image.copy()
                    tmp_pointer = tmp_pointer.rotate(-deg_to_target)
                    ret_image = ImageChops.add(ret_image, tmp_pointer)
            else:
                # if it's visible, plot it.
                if (
                    x_pos < self.render_size[0]
                    and x_pos > 0
                    and y_pos < self.render_size[1]
                    and y_pos > 0
                ):
                    _image = ImageChops.offset(
                        self.markers[symbol],
                        x_pos - (self.render_center[0] - 5),
                        y_pos - (self.render_center[1] - 5),
                    )
                    ret_image = ImageChops.add(ret_image, _image)

        return ret_image.rotate(roll).crop(self.render_crop)

    def update_projection(self, ra, dec):
        """
        Updates the shared projection used for various plotting
        routines
        """
        sky_pos = Star(
            ra=Angle(degrees=ra),
            dec_degrees=dec,
        )
        center = self.earth.observe(sky_pos)
        self.projection = build_stereographic_projection(center)

    def plot_starfield(self, ra, dec, roll, constellation_brightness=32):
        """
        Returns an image of the starfield at the
        provided RA/DEC/ROLL with or without
        constellation lines
        """
        self.update_projection(ra, dec)

        # Set star x/y for projection
        self.stars["x"], self.stars["y"] = self.projection(self.star_positions)

        # set start/end star x/y for const
        self.const_edges_df["sx"], self.const_edges_df["sy"] = self.projection(
            self.const_start_star_positions
        )
        self.const_edges_df["ex"], self.const_edges_df["ey"] = self.projection(
            self.const_end_star_positions
        )

        pil_image = self.render_starfield_pil(constellation_brightness)
        return pil_image.rotate(roll).crop(self.render_crop)

    def render_starfield_pil(self, constellation_brightness):
        ret_image = Image.new("L", self.render_size)
        idraw = ImageDraw.Draw(ret_image)

        # constellation lines first
        if constellation_brightness:
            # Rasterize edge start/end positions
            const_edges = self.const_edges_df.assign(
                sx_pos=self.const_edges_df["sx"] * self.pixel_scale
                + self.render_center[0],
                sy_pos=self.const_edges_df["sy"] * -1 * self.pixel_scale
                + self.render_center[1],
                ex_pos=self.const_edges_df["ex"] * self.pixel_scale
                + self.render_center[0],
                ey_pos=self.const_edges_df["ey"] * -1 * self.pixel_scale
                + self.render_center[1],
            )

            # filter for visibility
            visible_edges = const_edges[
                (
                    (const_edges["sx_pos"] > 0)
                    & (const_edges["sx_pos"] < self.render_size[0])
                    & (const_edges["sy_pos"] > 0)
                    & (const_edges["sy_pos"] < self.render_size[1])
                )
                | (
                    (const_edges["ex_pos"] > 0)
                    & (const_edges["ex_pos"] < self.render_size[0])
                    & (const_edges["ey_pos"] > 0)
                    & (const_edges["ey_pos"] < self.render_size[1])
                )
            ]

            for start_x, start_y, end_x, end_y in zip(
                visible_edges["sx_pos"],
                visible_edges["sy_pos"],
                visible_edges["ex_pos"],
                visible_edges["ey_pos"],
            ):
                idraw.line(
                    [start_x, start_y, end_x, end_y],
                    fill=(constellation_brightness),
                )

        # filter stars by magnitude
        visible_stars = self.stars[self.stars["magnitude"] < self.mag_limit]

        # Rasterize star positions
        visible_stars = visible_stars.assign(
            x_pos=visible_stars["x"] * self.pixel_scale + self.render_center[0],
            y_pos=visible_stars["y"] * -1 * self.pixel_scale + self.render_center[1],
        )
        # now filter by visiblity
        visible_stars = visible_stars[
            (visible_stars["x_pos"] > 0)
            & (visible_stars["x_pos"] < self.render_size[0])
            & (visible_stars["y_pos"] > 0)
            & (visible_stars["y_pos"] < self.render_size[1])
        ]

        for x_pos, y_pos, mag in zip(
            visible_stars["x_pos"], visible_stars["y_pos"], visible_stars["magnitude"]
        ):
            plot_size = (self.mag_limit - mag) / 3
            fill = 255
            if mag > 4.5:
                fill = 128
            if plot_size < 0.5:
                idraw.point((x_pos, y_pos), fill=fill)
            else:
                idraw.ellipse(
                    [
                        x_pos - plot_size,
                        y_pos - plot_size,
                        x_pos + plot_size,
                        y_pos + plot_size,
                    ],
                    fill=(255),
                )
        return ret_image
