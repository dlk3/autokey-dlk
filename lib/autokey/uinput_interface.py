# items avaliable on inputevents
# https://python-evdev.readthedocs.io/en/latest/apidoc.html#evdev.events.InputEvent
# sec, usec, type, code, value


import typing
import threading
import queue
import os
import grp
import evdev
import time
import select
import random
import re

from autokey.model.button import Button
from autokey.model.phrase import SendMode
from autokey.model.key import Key

import evdev
from evdev import ecodes as e

logger = __import__("autokey.logger").logger.get_logger(__name__)

from autokey.sys_interface.abstract_interface import AbstractSysInterface, queue_method
from autokey.gnome_interface import GnomeMouseReadInterface


class UInputInterface(threading.Thread, GnomeMouseReadInterface, AbstractSysInterface):
    """
    god this is complicated lol
    """

    inv_map = {}
    char_map = {
        "/":"KEY_SLASH", "'":"KEY_APOSTROPHE", ",":"KEY_COMMA", ".":"KEY_DOT", ";":"KEY_SEMICOLON",
        "[":"KEY_LEFTBRACE", "]":"KEY_RIGHTBRACE", "\\":"KEY_BACKSLASH", "=":"KEY_EQUAL", "-":"KEY_MINUS", "`": "KEY_GRAVE",
        " ":"KEY_SPACE", "\t": "KEY_TAB","\n": "KEY_ENTER"
    }
    #define as the left versions
    shift_key = 42
    ctrl_key = 29
    alt_key = 56
    meta_key = 125
    #TODO other modifiers
    #altgr_key
    #TODO should this be raw scan code or evdev equivalent? evdev makes more sense to me here
    #string.printable;
    #0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~ \t\n\r\x0b\x0c
    #
    shifted_chars = {
        "!":"KEY_1", "@":"KEY_2", "#":"KEY_3", "$":"KEY_4", "%":"KEY_5", "^":"KEY_6", "&": "KEY_7", "*": "KEY_8", "(":"KEY_9", ")":"KEY_0",
        "\"":"KEY_APOSTROPHE", ">": "KEY_DOT", "<": "KEY_COMMA", ":":"KEY_SEMICOLON", "{": "KEY_LEFTBRACE", "}": "KEY_RIGHTBRACE",
        "?": "KEY_SLASH", "+": "KEY_EQUAL", "_":"KEY_MINUS", "~":"KEY_GRAVE", "|":"KEY_BACKSLASH"
    }

    """
    str(AutoKey): str(evdev KEY_XXXX)
    """
    autokey_map = {
        "<left>": "KEY_LEFT", "<right>": "KEY_RIGHT", "<up>": "KEY_UP", "<down>": "KEY_DOWN",
        "<home>": "KEY_HOME", "<end>": "KEY_END", "<page_up>": "KEY_PAGEUP", "<page_down>": "KEY_PAGEDOWN",

        # universal modifiers
        "<shift>": "KEY_LEFTSHIFT", "<ctrl>": "KEY_LEFTCTRL", "<alt>": "KEY_LEFTALT",
        "<meta>": "KEY_LEFTMETA", "<hyper>": "KEY_LEFTMETA", "<super>": "KEY_LEFTMETA",

        # left modifiers
        "<left_shift>": "KEY_LEFTSHIFT", "<left_ctrl>": "KEY_LEFTCTRL", "<left_alt>": "KEY_LEFTALT", "<left_meta>": "KEY_LEFTMETA", "<left_hyper>": "KEY_LEFTMETA",

        # right modifiers
        "<right_shift>": "KEY_RIGHTSHIFT", "<right_ctrl>": "KEY_RIGHTCTRL", "<right_alt>": "KEY_RIGHTALT", "<right_meta>": "KEY_RIGHTMETA", "<right_hyper>": "KEY_RIGHTMETA",

        "<backspace>": "KEY_BACKSPACE", 
        
        
        "<enter>": "KEY_ENTER", "\n": "KEY_ENTER",
        "<tab>": "KEY_TAB", "\t": "KEY_TAB",
        "<escape>": "KEY_ESCAPE"
    }

    """
    str(evdev KEY_XXXX): str(AutoKey)
    """
    inv_autokey_map = {}

    uinput_modifiers_to_ak_map = {
        
        "KEY_LEFTSHIFT" : Key.LEFTSHIFT,
        "KEY_RIGHTSHIFT" : Key.RIGHTSHIFT,

        "KEY_LEFTCTRL": Key.LEFTCONTROL,
        "KEY_RIGHTCTRL": Key.RIGHTCONTROL,

        "KEY_LEFTALT": Key.LEFTALT,
        "KEY_RIGHTALT": Key.RIGHTALT,

        "KEY_LEFTMETA": Key.LEFTMETA,
        "KEY_RIGHTMETA": Key.RIGHTMETA,

    }

    uinput_keys_to_ak_map = {
        "KEY_LEFT": Key.LEFT,
        "KEY_RIGHT": Key.RIGHT,
        "KEY_UP": Key.UP,
        "KEY_DOWN": Key.DOWN,

        "KEY_HOME": Key.HOME,
        "KEY_END": Key.END,
        "KEY_PAGEUP": Key.PAGE_UP,
        "KEY_PAGEDOWN": Key.PAGE_DOWN,

        "KEY_BACKSPACE": Key.BACKSPACE,

        "KEY_ENTER": Key.ENTER,
        "KEY_TAB": Key.TAB,

        "KEY_ESC": Key.ESCAPE,

        "KEY_F1": Key.F1,
        "KEY_F2": Key.F2,
        "KEY_F3": Key.F3,
        "KEY_F4": Key.F4,
        "KEY_F5": Key.F5,
        "KEY_F6": Key.F6,
        "KEY_F7": Key.F7,
        "KEY_F8": Key.F8,
        "KEY_F9": Key.F9,
        "KEY_F10": Key.F10,
        "KEY_F11": Key.F11,
        "KEY_F12": Key.F12,

    }


    translation_map = {
        "slash": "/", "apostrophe": "'", "comma": ",", "dot": ".", "semicolon": ";",
        "space": " ", "tab": "\t", "enter": "\n", 
        "backslash": "\\", "equal": "=", "minus": "-", "grave": "`",
    }

    queue = queue.Queue()

    def __init__(self, mediator, app):
        threading.Thread.__init__(self)
        self.setDaemon(True)
        self.setName("UInputInterface-thread")
        self.mediator = mediator  # type: IoMediator
        self.app = app
        self.shutdown = False
        self.sending = False
        self.keyboard = None
        self.mouse = None
        time.sleep(1)

        # Event loop
        self.eventThread = threading.Thread(target=self.__eventLoop)

        ### UINPUT init
        self.validate()

        ### UINPUT Listener one for keyboard and eventually one for mouse
        # creating this before creating the new uinput device !important
        devices = self.get_devices()

        #TODO have to find a way to have user select which device to use (or try to find the right one)
        # should allow user to manually set the input devices in the settings.
        for dev in devices:
            logger.debug("Found device: {}".format(dev.name))
            if self.keyboard is None and re.search("keyboard", dev.name, re.IGNORECASE):
                logger.debug("Found keyboard: {}".format(dev.name))
                self.keyboard = self.grab_device(devices, dev.name)
                self.keyboard.grab()
            elif self.mouse is None and re.search("mouse", dev.name, re.IGNORECASE):
                logger.debug("Found mouse: {}".format(dev.name))
                self.mouse = self.grab_device(devices, dev.name)
        #self.keyboard = self.grab_device(devices, "ZSA Technology Labs ErgoDox EZ")
        #self.mouse = self.grab_device(devices, "Logitech MX Master 3")
        # self.keyboard = self.grab_device(devices, "AT Translated Set 2 keyboard")
        # self.keyboard.grab() # exclusive grab, no other process can use it, instead pass through input events via uinput
        # self.mouse = self.grab_device(devices, "VirtualBox mouse integration")

        if self.mouse is None or self.keyboard is None:
            logger.error("Unable to find mouse or keyboard")
            raise Exception("Unable to find mouse or keyboard")

        self.devices = [self.keyboard, self.mouse]
        logger.debug("Keyboard: {}, Path: {}".format(self.keyboard.name, self.keyboard.path))
        logger.debug("Mouse: {}, Path: {}".format(self.mouse.name, self.mouse.path))
        # logger.debug("Keyboard DIR: {}".format(dir(self.keyboard)))
        
        self.devices = {dev.fd: dev for dev in self.devices}


        try:
            # mouse = "/dev/input/event12"
            # keyboard = "/dev/input/event4"
            # creating a uinput device with the combined capabilities of the user's mouse and keyboard
            # this will undoubtedly cause issues if user attempts to send signals not supported by their devices
            self.ui = evdev.UInput.from_device(self.mouse.path, self.keyboard.path, name="autokey mouse and keyboard")

        except Exception as ex:
            logger.error("Unable to create UInput device. {}".format(ex))
            logger.error("Check out how to resolve this issue here: https://github.com/philipl/evdevremapkeys/issues/24")
            #print("Unable to create UInput device. {}".format(ex))

        GnomeMouseReadInterface.__init__(self)


        self.inv_map = self.__reverse_mapping(e.keys)
        self.inv_autokey_map = self.__reverse_mapping(self.autokey_map)
        logger.debug("Inverted Map:", self.inv_map)

        # Event listener
        self.listenerThread = threading.Thread(target=self.__flush_events_loop)

        self.__initMappings()

        self.eventThread.start()
        self.listenerThread.start()


    def __reverse_mapping(self, dictionary):
        map = {}
        for item in dictionary.items():
            if type(item[1]) == list:
                continue
            else:
                map[item[1]] = item[0]
        return map

    def validate(self):
        """
        Checks that UInput will work correctly

        Prints out error message currently if that fails.
        """
        #TODO run checks that uinput will work as expected.
        user = os.getlogin()
        input_group = grp.getgrnam("input")
        if user in input_group.gr_mem or os.geteuid()==0:
            logger.info("input User membership good!")
        else:
            logger.error("User not in input group add yourself or run program as root")
            logger.error(f"sudo usermod -a -G input {user}")
            raise Exception("User not in input group add yourself or run program as root")

    @queue_method(queue)
    def clear_held_keys(self):
        self.held_keys = self.keyboard.active_keys()
        logger.debug("Clearing keys: {}".format(self.held_keys))
        for key in self.held_keys:
            self.ui.write(e.EV_KEY, key, 0)

    @queue_method(queue)
    def reapply_keys(self):
        #self.held_keys = self.keyboard.active_keys()
        logger.debug("Reapply held keys: {}".format(self.held_keys))
        for key in self.held_keys:
            self.ui.write(e.EV_KEY, key, 1)


    @queue_method(queue)
    def send_string(self, string):
        """
        Send a string of characters to be sent via the keyboard
        Usage: C{ukeyboard.send_keys("This is a test string")}
        :param string: str or list, use list if you want to send characters like KEY_LEFTCTRL
        """
        #print(keys)
        # if type(string) == list:
        #     for subset in string:
        #         self.send_string(subset)
        #     return
        if type(string) == str and "KEY_" in string[:4]:
            self.__send_key(string)
            return
        if type(string) == int:
            self.__send_key(string)
            return
        
        for key in string:
            self.__send_key(key)

    def paste_string(self, string, paste_command: SendMode):
        raise NotImplementedError

    @queue_method(queue)
    def send_key(self, key_name):
        self.__send_key(key_name)

    @queue_method(queue)
    def send_evdev_code(self, type_, code, value):
        self.ui.write(type_, code, value)

    def send_evdev_code_raw(self, type_, code, value):
        """
        Immediately sends the event instead of adding it to the queue.
        """
        self.ui.write(type_, code, value)

    def __send_key(self, key, shifted=False, syn=True):
        # print(f"Writing {key}")
        evdev_keycode, shifted_ = self.translate_to_evdev(key)
        #print(key, evdev_keycode, shifted_)
        evdev_key = e.keys[evdev_keycode]
        if shifted_:
            shifted=True

        #print(f"Keycode: {evdev_keycode}, Key: {evdev_key}", shifted_, shifted)
        logger.debug("Sending key: {}, Shifted: {}, Untranslated: {}".format(evdev_key, shifted_, key))
        if shifted:
            self.ui.write(e.EV_MSC, e.MSC_SCAN, self.shift_key)
            self.ui.write(e.EV_KEY, self.shift_key, 1)
            self.syn_raw()
            
        self.ui.write(e.EV_MSC, e.MSC_SCAN, self.inv_map[evdev_key])
        self.ui.write(e.EV_KEY, self.inv_map[evdev_key], 1)
        if syn : self.syn_raw()

        self.ui.write(e.EV_MSC, e.MSC_SCAN, self.inv_map[evdev_key])
        self.ui.write(e.EV_KEY, self.inv_map[evdev_key], 0)
        if syn : self.syn_raw()

        if shifted:
            self.ui.write(e.EV_MSC, e.MSC_SCAN, self.shift_key)
            self.ui.write(e.EV_KEY, self.shift_key, 0)
            self.syn_raw()

    def get_delay(self):
        #return 0.01+random.randrange(1,10)/1000
        #return 0.001
        return 0.005

    @queue_method(queue)
    def syn(self):
        self.ui.write(e.EV_SYN, 0, 0)
        time.sleep(self.get_delay()) #important to sleep here, otherwise the modifiers can be lost
        
    def syn_raw(self):
        self.ui.write(e.EV_SYN, 0, 0)
        time.sleep(self.get_delay()) #important to sleep here, otherwise the modifiers can be lost

    @queue_method(queue)
    def press_key(self, keyName):
        self.__press_key(keyName, True)

    @queue_method(queue)
    def release_key(self, keyName):
        self.__release_key(keyName, True)

    def __release_key(self, key, syn=False):
        evdev_keycode, shifted_ = self.translate_to_evdev(key)
        #print(key, evdev_keycode, shifted_)
        evdev_key = e.keys[evdev_keycode]
        if shifted_:
            shifted=True

        self.ui.write(e.EV_MSC, e.MSC_SCAN, self.inv_map[evdev_key])
        self.ui.write(e.EV_KEY, self.inv_map[evdev_key], 0)
        if syn: self.syn_raw()

    def  __press_key(self, key, syn=False):
        evdev_keycode, shifted_ = self.translate_to_evdev(key)
        #print(key, evdev_keycode, shifted_)
        evdev_key = e.keys[evdev_keycode]
        if shifted_:
            shifted=True
        self.ui.write(e.EV_MSC, e.MSC_SCAN, self.inv_map[evdev_key])
        self.ui.write(e.EV_KEY, self.inv_map[evdev_key], 1)
        if syn: self.syn_raw()

    
    # def wait_for_keypress(self, timeout=None):
    #     raise NotImplementedError

    
    # def wait_for_keyevent(self, timeout=None):
    #     raise NotImplementedError

    def __initMappings(self):
        """
        Grabs hotkeys. Under X11 this means blocking the hotkeys from sending to individual applications. 
        Not sure if this can be accomplished via uinput/wayland?
        """
        pass
        self.__grab_hotkeys()

    def __grab_hotkeys(self):
        self.__grab_ungrab_all_hotkeys(grab=True)

    def __ungrab_all_hotkeys(self):
        self.__grab_ungrab_all_hotkeys(grab=False)

    def __grab_ungrab_all_hotkeys(self, grab=True):
        c = self.app.configManager
        logger.debug("Starting UInput hotkey grabber:")
        hotkeys = c.hotKeys + c.hotKeyFolders

        for item in hotkeys:
            logger.debug("Hotkey: {}, Modifier: {}".format(item.hotKey, item.modifiers))

    def isModifier(self, keyCode):
        """
        """
        for item in self.uinput_modifiers_to_ak_map:
            if item == keyCode:
                logger.debug("isModifier: {}, True".format(keyCode))
                return True
        logger.debug("isModifier: {}, False".format(keyCode))
        return False
        # if keyCode in self.uinput_to_ak_map:
            # return True
        # print(keyCode, self.modifiers, self.translate_to_evdev(keyCode))
        # print(self.inv_map[keyCode[1]])
    
    @queue_method(queue)
    def handle_keypress(self, keyCode):
        window_info = self.mediator.windowInterface.get_window_info()
        event_type = evdev.categorize(keyCode)
        logger.debug("handle_keypress: KeyState:{}, Un:{}".format(event_type.keystate, event_type.keycode))
        if self.isModifier(event_type.keycode):
            # need to translate event_type.keycode to a Key. value from an evdev keycode
            self.mediator.handle_modifier_down(self.uinput_modifiers_to_ak_map[event_type.keycode])
        # else: 
            # self.mediator.handle_keypress(self.translate_to_evdev(event_type.keycode), window_info)


    @queue_method(queue)
    def handle_keyrelease(self, keyCode):
        window_info = self.mediator.windowInterface.get_window_info() #not used
        event_type = evdev.categorize(keyCode)
        logger.debug("handle_keyrelease: KeyState:{}, Un:{}".format(event_type.keystate, event_type.keycode))
        if self.isModifier(event_type.keycode):
            self.mediator.handle_modifier_up(self.uinput_modifiers_to_ak_map[event_type.keycode])
        else:
            self.mediator.handle_keypress(self.translate_to_evdev(event_type.keycode), window_info)

    def __flush_events_loop(self):
        logger.debug("__flushEvents: Entering event loop.")
        while True:
            try:
                #logger.debug("__flushEvents: Waiting for events.")
                self.__flush_events()
                if self.shutdown:
                    break
            except Exception as e:
                logger.exception(
                    "Some exception occurred in the flush events loop: {}".format(e))
        logger.debug("Left event loop.")

    def __flush_events(self):
        r,w,x = select.select(self.devices, [], [])
        for fd in r:
            for event in self.devices[fd].read(): # type: ignore
                event_type = evdev.categorize(event)
                held = self.keyboard.active_keys(verbose=True)

                if type(event_type) is evdev.KeyEvent:
                    
                    # if event_type.scancode in iter(Button): #is a mouse button 
                        # continue
                        #logger.debug("__flush_events: Button State: {}, Button Code: {}".format(event_type.keystate, event_type.keycode))

                    if event_type.keystate == 1 : #key down
                        logger.debug("__flush_events: Key State: {}, Key Code: {}, Scan Code: {}".format(event_type.keystate, event_type.keycode, event_type.scancode))
                        logger.debug("Held: {}".format(held))
                        #logger.debug("Key: {}".format(event_type))
                        self.handle_keypress(event)
                    elif event_type.keystate == 0: #key up
                        self.handle_keyrelease(event)
                    elif event_type.keystate == 2: # hold event
                        #TODO: handle hold event
                        pass
                    #TODO can also handle scroll events here
                        
                    
                elif type(event_type) is evdev.RelEvent:
                    pass
                    #mouse movement event
                    #logger.debug("Mouse: {}".format(event_type))
                    #if event_type.
                    #self.handle_mouseclick(event)
                
                
                # pass through to autokey uinput device
                if not self.sending and fd == self.keyboard.fd:
                    self.ui.write(event.type, event.code, event.value)


    def flush(self):
        """
        Currently not needed for UInput? But is a part of the AbstractSysInterface definition
        """
        pass


    def __eventLoop(self):
        while True:
            method, args = self.queue.get()

            if method is None and args is None:
                break
            elif method is not None and args is None:
                logger.debug("__eventLoop: Got method {} with None arguments!".format(method))
            try:
                method(*args)
            except Exception as e:
                logger.exception("Error in UInput event loop thread: {}".format(e))

            self.queue.task_done()


    def grab_hotkey(self, item):
        pass


    def ungrab_hotkey(self, item):
        pass


    @queue_method(queue)
    def grab_keyboard(self, ):
        #TODO: something weird is happening with grab/ungrab
        #self.keyboard.grab()

        self.sending = True
        
        pass

    @queue_method(queue)
    def ungrab_keyboard(self, ):
        #TODO: need to implement this
        pass
        self.sending = False
        #self.keyboard.ungrab()

    def handle_mouseclick(self, clickEvent):
        self.mediator.handle_mouse_click(clickEvent)

    def on_keys_changed(self, ):
        raise NotImplementedError

    @queue_method(queue)
    def send_modified_key(self, key, modifiers):
        """
        modifiers are expected to be autokey style
        """
        from evdev import ecodes as e # not sure why this is needed here? Decorator maybe doing something weird?
        logger.debug(f"Send modified key: {key}, Modifiers: {modifiers}")
        evdev_keycode, shifted = self.translate_to_evdev(key)
        evdev_key = e.keys[evdev_keycode]


        try:
            # down mods
            for mod in modifiers:
                #convert to KEY_ modifier
                evdev_mod_key = self.autokey_map[mod]
                self.ui.write(e.EV_MSC, e.MSC_SCAN, self.inv_map[evdev_mod_key])
                self.ui.write(e.EV_KEY, self.inv_map[evdev_mod_key], 1)
                self.syn_raw()
            # send key up
            self.ui.write(e.EV_MSC, e.MSC_SCAN, self.inv_map[evdev_key])
            self.ui.write(e.EV_KEY, self.inv_map[evdev_key], 1)
            self.syn_raw()

            # send key down
            self.ui.write(e.EV_MSC, e.MSC_SCAN, self.inv_map[evdev_key])
            self.ui.write(e.EV_KEY, self.inv_map[evdev_key], 0)
            self.syn_raw()


            # up mods
            for mod in modifiers:
                #convert to KEY_ modifier
                evdev_mod_key = self.autokey_map[mod]
                self.ui.write(e.EV_MSC, e.MSC_SCAN, self.inv_map[evdev_mod_key])
                self.ui.write(e.EV_KEY, self.inv_map[evdev_mod_key], 0)
                self.syn_raw()
            
            pass
        except Exception as e:
            logger.warning("Error sending modified key %r %r: %s", modifiers, keyName, str(e))

    
    def cancel(self):
        logger.debug("UInputInterface: Try to exit event thread.")
        self.queue.put_nowait((None, None))
        logger.debug("UInputInterface: Event thread exit marker enqueued.")
        self.shutdown = True
        logger.debug("UInputInterface: Shutdown flag set.")

        self.listenerThread.join()
        self.eventThread.join()

        self.join()


    def fake_keydown(self, key):
        """
        No such thing as "fake keydown" in UInput. This is passing through to press_key.
        """
        self.press_key(key)
    
    def fake_keypress(self, key):
        """
        No such thing as "fake keypress" in UInput. This is passing through to send_key.
        """
        #self.press_key(key)
        self.send_key(key)
    
    def fake_keyup(self, key):
        """
        No such thing as "fake keyup" in UInput. This is passing through to release_key.
        """
        self.release_key(key)
    
    def get_devices(self):
        return [evdev.InputDevice(path) for path in evdev.list_devices()]

    def grab_device(self, devices, descriptor):
        #determine if descriptor is a path or a name
        return_device = None
        if len(descriptor) <= 2: #assume that people don't have more than 99 input devices
            descriptor = "/dev/input/event"+descriptor
        if "/dev/" in descriptor: #assume function was passed a path
            for device in devices:
                if descriptor==device.path:
                    device.close()
                    return_device = evdev.InputDevice(device.path)
                else:
                    device.close()
        else: #assume that function was passed a plain text name
            for device in devices:
                if descriptor==device.name:
                    device.close()
                    return_device = evdev.InputDevice(device.path)
                else:
                    device.close()

        return return_device
    

    def translate_to_evdev(self, key):
        """
        Takes an input and tries to convert it to a evdev key code, primarily for internal use
        Determination of type is based primarily on the key type in order;
        if key is type str and the first 4 letters have "KEY_" we assume it is an evdev keycode and return from the inverted mapping of the ecodes.keys
        elif the key is type int we will assume if is a raw evdev value and return it
        elif len(key) == 1, assume that this is an ascii char, check if "KEY_"+key exists in any of the character maps and process accordingly
        return (0, false)
        """
        if type(key)==str and "KEY_" in key[:4]: #if it is a "KEY_A" type value return evdev int from map
            # print("Type str")
            return self.inv_map[key], False
        elif type(key)==int: #if it is type int it should be a evdev raw value
            # print("Type int")
            return key, False
        elif len(key)==1:
            #print("Type single char", key)
            evdev_key = "KEY_"+key.upper()
            if key in self.shifted_chars:
                return self.inv_map[self.shifted_chars[key]], True
            elif key in self.char_map:
                return self.inv_map[self.char_map[key]], False
            elif evdev_key in self.inv_map:
                return self.inv_map[evdev_key], key.isupper()
            elif evdev_key in self.char_map:
                return self.inv_map[self.char_map[evdev_key]], key.isupper()
        elif type(key)==str and key in self.autokey_map:
            # print("Type <autokey>", key)
            return self.inv_map[self.autokey_map[key]], False
        return (0, False)
    
    def initialise(self):
        pass

    def run(self):
        pass

    def __processEvent(self):
        pass

    def lookup_string(self, keyCode, shifted, num_lock, altGrid):
        """
        
        """
        logger.debug("lookup_string: keyCode:{}, Translated:{}, Shifted: {}".format(keyCode, self.translate_to_evdev(keyCode[0]), shifted))

        #if not shifted and not num_lock and not altGrid:
        #TODO handle special keys like backslash etc.
        code, shifted_ = self.translate_to_evdev(keyCode[0])
        evdev_name = e.keys[code]
        # modifiers
        if evdev_name in self.uinput_modifiers_to_ak_map:
            logger.debug("Modifier: {}".format(evdev_name))
            return self.uinput_modifiers_to_ak_map[evdev_name]
        # "keys" like f12 ESC etc.
        if evdev_name in self.uinput_keys_to_ak_map:
            logger.debug("Key: {}".format(evdev_name))
            return self.uinput_keys_to_ak_map[evdev_name]
        character = evdev_name.replace("KEY_", "")
        if character.lower() in self.translation_map:
            character = self.translation_map[character.lower()]
        if shifted:
            return character.upper()
        else:
            return character.lower()
        #return self.translate_to_evdev(keyCode[0])
        
        #return e.keys[self.translate_to_evdev(keyCode)[0]]
