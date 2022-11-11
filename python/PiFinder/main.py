#!/usr/bin/python
# -*- coding:utf-8 -*-
"""
This module is the main entry point for PiFinder it:
* Initializes the display
* Spawns keyboard process
* Sets up time/location via GPS
* Spawns camers/solver process
* then runs the UI loop

"""
from PIL import Image, ImageDraw, ImageFont, ImageChops, ImageOps
from multiprocessing import Process, Queue
from multiprocessing.managers import BaseManager
import time
import queue

from luma.core.interface.serial import spi
from luma.core.render import canvas
from luma.oled.device import ssd1351

import keyboard
import camera
import solver

from uimodules import UIPreview, UIConsole

serial = spi(device=0, port=0)
device = ssd1351(serial)


def set_brightness(level):
    """
    Sets oled brightness
    0-255
    """
    device.contrast(level)


class StateManager(BaseManager):
    pass


class SharedStateObj:
    def __init__(self):
        self.__solve_state = None
        self.__last_image_time = 0
        self.__solve = None
        self.__imu = None

    def solve(self):
        return self.__solve

    def set_solve(self, v):
        self.__solve = v

    def last_image_time(self):
        return self.__last_image_time

    def set_last_image_time(self, v):
        self.__last_image_time = v


StateManager.register("SharedState", SharedStateObj)
StateManager.register("NewImage", Image.new)


def main():
    """
    Get this show on the road!
    """
    # init screen
    screen_brightness = 200
    set_brightness(screen_brightness)
    console = UIConsole(device, None, None, None)
    console.write("Starting....")
    console.update()

    # multiprocessing.set_start_method('spawn')
    # spawn keyboard service....
    console.write("   Keyboard")
    console.update()
    keyboard_queue = Queue()
    keyboard_process = Process(target=keyboard.run_keyboard, args=(keyboard_queue,))
    keyboard_process.start()

    # spawn imaging service
    with StateManager() as manager:
        shared_state = manager.SharedState()

        console.write("   Camera")
        console.update()
        camera_command_queue = Queue()
        camera_image = manager.NewImage("RGB", (512, 512))
        image_process = Process(
            target=camera.get_images,
            args=(shared_state, camera_image, camera_command_queue),
        )
        image_process.start()

        # Wait for camera to start....
        time.sleep(2)

        # Solver
        console.write("   Solver")
        console.update()
        solver_process = Process(
            target=solver.solver, args=(shared_state, camera_image)
        )
        solver_process.start()

        # Start main event loop
        console.write("   Event Loop")
        console.update()

        # init UI Modes
        command_queues = {
            "camera": camera_command_queue,
        }
        ui_modes = [
            console,
            UIPreview(device, camera_image, shared_state, command_queues),
        ]
        ui_mode_index = 1

        while True:
            try:
                keycode = keyboard_queue.get(block=False)
            except queue.Empty:
                keycode = None

            if keycode != None:
                print(f"{keycode =}")
                if keycode > 99:
                    # Special codes....
                    if keycode == keyboard.ALT_UP:
                        screen_brightness = screen_brightness + 10
                        if screen_brightness > 255:
                            screen_brightness = 255
                        set_brightness(screen_brightness)
                        console.write("Brightness: " + str(screen_brightness))

                    if keycode == keyboard.ALT_DN:
                        screen_brightness = screen_brightness - 10
                        if screen_brightness < 1:
                            screen_brightness = 1
                        set_brightness(screen_brightness)
                        console.write("Brightness: " + str(screen_brightness))
                elif keycode == keyboard.A:
                    # A key, mode switch
                    ui_mode_index += 1
                    if ui_mode_index >= len(ui_modes):
                        ui_mode_index = 0
                    ui_modes[ui_mode_index].active()

                else:
                    if keycode < 10:
                        ui_modes[ui_mode_index].key_number(keycode)

                    elif keycode == keyboard.UP:
                        ui_modes[ui_mode_index].key_up()

                    elif keycode == keyboard.DN:
                        ui_modes[ui_mode_index].key_down()

                    elif keycode == keyboard.GO:
                        ui_modes[ui_mode_index].key_enter()

                    elif keycode == keyboard.B:
                        ui_modes[ui_mode_index].key_b()

                    elif keycode == keyboard.C:
                        ui_modes[ui_mode_index].key_c()

                    elif keycode == keyboard.D:
                        ui_modes[ui_mode_index].key_d()

            ui_modes[ui_mode_index].update()


if __name__ == "__main__":
    main()
