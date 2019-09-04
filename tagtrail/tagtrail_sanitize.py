#  tagtrail: A bundle of tools to organize a minimal-cost, trust-based and thus
#  time efficient accounting system for small, self-service community stores.
#
#  Copyright (C) 2019, Simon Greuter
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.
from tkinter import *
from tkinter import ttk
import re
from PIL import ImageTk,Image  
from sheets import ProductSheet
from database import Database

lista = ['a', 'actions', 'additional', 'also', 'an', 'and', 'angle', 'are', 'as', 'be', 'bind', 'bracket', 'brackets', 'button', 'can', 'cases', 'configure', 'course', 'detail', 'enter', 'event', 'events', 'example', 'field', 'fields', 'for', 'give', 'important', 'in', 'information', 'is', 'it', 'just', 'key', 'keyboard', 'kind', 'leave', 'left', 'like', 'manager', 'many', 'match', 'modifier', 'most', 'of', 'or', 'others', 'out', 'part', 'simplify', 'space', 'specifier', 'specifies', 'string;', 'that', 'the', 'there', 'to', 'type', 'unless', 'use', 'used', 'user', 'various', 'ways', 'we', 'window', 'wish', 'you']


class AutocompleteEntry(ttk.Combobox):
    def __init__(self, lista, releaseFocus, *args, **kwargs):
        Entry.__init__(self, *args, **kwargs)
        self.lista = lista
        self.releaseFocus = releaseFocus
        self.var = self["textvariable"]
        if self.var == '':
            self.var = self["textvariable"] = StringVar()

        self.var.trace('w', self.changed)
        self.bind("<Return>", self.selection)
        self.bind("<Up>", self.up)
        self.bind("<Down>", self.down)
        self.bind("<Left>", self.left)
        self.bind("<Right>", self.right)
        self.bind("<BackSpace>", self.backspace)
        self.bind("<Tab>", self.tab)

        self.prev_val = ""
        self.lb_up = False

    def changed(self, name, index, mode):
        print('changed var=',self.var.get())
        if self.var.get() == '':
            if self.lb_up:
                self.lb.destroy()
                self.lb_up = False
        else:
            words = self.comparison(self.var.get())
            print(words)
            if len(words) == 1 and len(self.prev_val) < len(self.var.get()):
                if self.lb_up:
                    self.lb.destroy()
                    self.lb_up = False
                self.delete(0, END)
                self.insert(0, words[0])

            elif words:
                if len(words) > 1 and self.longestCommonPrefix(words) != self.var.get().upper():
                    self.delete(0, END)
                    self.insert(0, self.longestCommonPrefix(words))
                    print('self.longestCommonPrefix(words)=',
                            self.longestCommonPrefix(words))

                if not self.lb_up:
                    self.lb = Listbox()
                    self.lb.place(x=self.winfo_x(), y=self.winfo_y()+self.winfo_height())
                    self.lb_up = True

                self.lb.delete(0, END)
                for w in words:
                    self.lb.insert(END,w)
            else:
                self.var.set(self.prev_val)

        self.prev_val = self.var.get()

    def selection(self, event):
        if self.lb_up:
            self.var.set(self.lb.get(ACTIVE))
            self.lb.destroy()
            self.lb_up = False
            self.icursor(END)

    def up(self, event):
        if self.lb_up:
            if self.lb.curselection() == ():
                index = '0'
            else:
                index = self.lb.curselection()[0]
            if index != '0':
                self.lb.selection_clear(first=index)
                index = str(int(index)-1)
                self.lb.selection_set(first=index)
                self.lb.activate(index)
        else:
            return self.releaseFocus(event)


    def down(self, event):
        if self.lb_up:
            if self.lb.curselection() == ():
                index = '0'
            else:
                index = self.lb.curselection()[0]
            if index != END:
                self.lb.selection_clear(first=index)
                index = str(int(index)+1)
                self.lb.selection_set(first=index)
                self.lb.activate(index)
        else:
            return self.releaseFocus(event)

    def right(self, event):
        if self.lb_up:
            return "break"
        else:
            return self.releaseFocus(event)

    def left(self, event):
        if self.lb_up:
            return "break"
        else:
            return self.releaseFocus(event)

    def tab(self, event):
        if self.lb_up:
            return "break"
        else:
            return self.releaseFocus(event)

    def backspace(self, event):
        if self.var.get() == '':
            if self.lb_up:
                self.lb.destroy()
                self.lb_up = False
        else:
            word = self.var.get()
            numOptions = len(self.comparison(word))
            prefixes = [word[0:i] for i in range(len(word)+1)]
            for p in sorted(prefixes, reverse=True):
                if len(p) == 0 or numOptions < len(self.comparison(p)):
                    self.var.set(p)
                    break
        return "break"

    def longestCommonPrefix(self, words):
        word = words[0].upper()
        prefixes = [word[0:i] for i in range(len(word)+1)]
        for p in sorted(prefixes, reverse=True):
            isPrefix = [(w.upper().find(p) == 0) for w in words]
            if len(p) == 0 or False not in isPrefix:
                return p

    def comparison(self, word):
        if not self.lista:
            return [word]
        return [w for w in self.lista if w.upper().find(word.upper()) == 0]

class InputSheet(ProductSheet):
    def __init__(self, name, unit, price, quantity, database):
        super().__init__(name, unit, price, quantity)
        self._widget_to_box = {}
        self._box_to_widget = {}
        for box in self._boxes:
            if box.name == "nameBox":
                choices = [v._description for v in database._products.values()]
            elif box.name == "unitBox":
                choices = []
            elif box.name == "priceBox":
                choices = []
            elif box.name.find("dataBox") != -1:
                choices = database._members.keys()
            else:
                continue

            (x1, y1) = box.pt1
            x1, y1 = x1*ratio, y1*ratio
            (x2, y2) = box.pt2
            x2, y2 = x2*ratio, y2*ratio
            entry = AutocompleteEntry(choices, self.switchFocus, root)
            entry.place(x=canvas_w+x1, y=y1, w=x2-x1, h=y2-y1)
            self._widget_to_box[entry] = box
            self._box_to_widget[box] = entry

    def switchFocus(self, event):
        if str(event.type) != "KeyPress":
            return event

        # cudos to https://www.daniweb.com/programming/software-development/code/216830/tkinter-keypress-event-python
        if event.char == event.keysym:
            # normal key, not handled here
            return event
        else:
            # punctuation or special key, distinguish by event.keysym
            box=self._widget_to_box[event.widget]
            if event.keysym == "Tab":
                i=self._boxes.index(box) + 1
                if i+1 == len(self._boxes): i = 0
                next_box = self._boxes[i]
                self._box_to_widget[next_box].focus_set()
                return "break"
            elif event.keysym in ["Up", "Down", "Left", "Right"]:
                neighbourBox = self.neighbourBox(box, event.keysym)
                if neighbourBox:
                    self._box_to_widget[neighbourBox].focus_set()
                return "break"
            else:
                return event

if __name__ == '__main__':
    root = Tk()
    window_w, window_h = 1366, 768
    root.geometry(str(window_w)+'x'+str(window_h))

    canvas_w, canvas_h = window_w/2, window_h
    canvas = Canvas(root, 
               width=canvas_w, 
               height=canvas_h)
    canvas.place(x=0, y=0)
    img = Image.open("data/scans/test0002.jpg")
    o_w, o_h = img.size
    ratio = min(canvas_h / o_h, canvas_w / o_w)
    img = img.resize((int(ratio * o_w), int(ratio * o_h)), Image.BILINEAR)
    img = ImageTk.PhotoImage(img)
    canvas.create_image(0,0, anchor=NW, image=img)


    dataFilePath = 'data/database/{}'
    db = Database(dataFilePath.format('mitglieder.csv'),
            dataFilePath.format('produkte.csv'))
    sheet = InputSheet("not", "known", "yet",
            ProductSheet.maxQuantity(), db)

    root.mainloop()
