from __future__ import absolute_import, print_function
from qtpy import  QtCore, QtWidgets
import pyqtgraph
import numpy as np
from collections import OrderedDict
import json
import sys
import logging
from ScopeFoundry.helper_funcs import get_logger_from_class

if sys.version_info[0] == 3:
    unicode = str

class LoggedQuantity(QtCore.QObject):
    """
    LoggedQuantity objects are containers that wrap settings. These settings may be a number (integer or float) 
    or a string and occasionally small arrays of them. 
    
    These objects emit signals when changed and can be connected bidirectionally to Qt Widgets. 
    
    In ScopeFoundry we represent the values in an object called a `LoggedQuantity`. 
    A `LoggedQuantity` is a class that contains a value, a `bool`, `float`, `int`, `str` etc 
    that is part of an application's state. In the case of microscope and equipment control, 
    these also can represent the state of a piece of hardware. These are very useful objects 
    because the are the central location of the value contained within. All graphical interface views 
    will be guaranteed to be consistent with the `LQ` state. The data of these quantities will also 
    be saved in datafiles created by ScopeFoundry.
    
    """

    updated_value = QtCore.Signal((float,),(int,),(bool,), (), (str,),) # signal sent when value has been updated
    updated_text_value = QtCore.Signal(str) # signal sent when value has been updated, sends text representation
    updated_choice_index_value = QtCore.Signal(int) # emits the index of the value in self.choices
    
    updated_min_max = QtCore.Signal((float,float),(int,int), (),) # signal sent when min max range updated
    updated_readonly = QtCore.Signal((bool,), (),) # signal sent when read only (ro) status has changed
    
    def __init__(self, name, dtype=float, 
                 hardware_read_func=None, hardware_set_func=None, 
                 initial=0, fmt="%g", si=False,
                 ro = False, # read only flag
                 unit = None,
                 spinbox_decimals = 2,
                 spinbox_step=0.1,
                 vmin=-1e12, vmax=+1e12, choices=None):
        QtCore.QObject.__init__(self)
        
        self.name = name
        self.dtype = dtype
        self.val = dtype(initial)
        self.hardware_read_func = hardware_read_func
        self.hardware_set_func = hardware_set_func
        self.fmt = fmt # string formatting string. This is ignored if dtype==str
        self.si   = si # will use pyqtgraph SI Spinbox if True
        self.unit = unit
        self.vmin = vmin
        self.vmax = vmax
        self.choices = self._expand_choices(choices) # should be tuple [ ('name', val) ... ] or simple list [val, val, ...]
        self.ro = ro # Read-Only
        
        self.log = get_logger_from_class(self)
        
        if self.dtype == int:
            self.spinbox_decimals = 0
        else:
            self.spinbox_decimals = spinbox_decimals
        self.reread_from_hardware_after_write = False
        
        if self.dtype == int:
            self.spinbox_step = 1
        else:
            self.spinbox_step = spinbox_step
        
        self.oldval = None
        
        self._in_reread_loop = False # flag to prevent reread from hardware loops
        
        self.widget_list = []
        self.listeners = []
        
    def coerce_to_type(self, x):
        """force x to dtype of the LQ"""        
        return self.dtype(x)
        
    def _expand_choices(self, choices):
        if choices is None:
            return None
        expanded_choices = []
        for c in choices:
            if isinstance(c, tuple):
                name, val = c
                expanded_choices.append( ( str(name), self.dtype(val) ) )
            else:
                expanded_choices.append( ( str(c), self.dtype(c) ) )
        return expanded_choices


    def read_from_hardware(self, send_signal=True):
        if self.hardware_read_func:
            self.oldval = self.val
            val = self.hardware_read_func()
            #print "read_from_hardware", self.name, val
            self.val = self.coerce_to_type(val)
            if send_signal:
                self.send_display_updates()
        return self.val

    def value(self):
        "return stored value"
        return self.val

    @QtCore.Slot(str)
    @QtCore.Slot(float)
    @QtCore.Slot(int)
    @QtCore.Slot(bool)
    @QtCore.Slot()
    def update_value(self, new_val=None, update_hardware=True, send_signal=True, reread_hardware=None):
        """
        Update stored value with new_val
         
        Change value of LQ and emit signals to inform listeners of change 
        
        if *update_hardware* is true: call connected hardware_set_func
        
        Options:
        update_hardware (default True): calls hardware_set_func if defined
        send_signal (default True): sends out QT signals on change
        reread_hardware: read from hardware after writing to hardware to ensure change         
        
        """
        #print "LQ update_value", self.name, self.val, "-->",  new_val
        if new_val is None:
            #print "update_value {} new_val is None. From Sender {}".format(self.name, self.sender())
            new_val = self.sender().text()

        self.oldval = self.coerce_to_type(self.val)
        new_val = self.coerce_to_type(new_val)
        
        #print "LQ update_value1", self.name

        if self.same_values(self.oldval, new_val):
            #print "same_value so returning", self.oldval, new_val
            self._in_reread_loop = False #once value has settled in the event loop, re-enable reading from hardware
            return
        
        self.val = new_val

        #print "LQ update_value2", self.name
        
        if reread_hardware is None:
            reread_hardware = self.reread_from_hardware_after_write
        
        #print "called update_value", self.name, new_val, reread_hardware
        if update_hardware and self.hardware_set_func and not self._in_reread_loop:
            self.hardware_set_func(self.val)
            if reread_hardware:
                # re-reading from hardware can set off a loop of setting 
                # and re-reading from hardware if hardware readout is not
                # exactly the requested value. temporarily disable rereading
                # from hardware until value in LoggedQuantity has settled
                self._in_reread_loop = True 
                self.read_from_hardware(send_signal=False) # changed send_signal to false (ESB 2015-08-05)
        if send_signal:
            self.send_display_updates()
            
    def send_display_updates(self, force=False):
        """
        emit updated_value signals if value has changed.
        
        *force* will emit signals regardless of value change. 
        """
        self.log.debug("send_display_updates: {} force={}".format(self.name, force))
        if (not self.same_values(self.oldval, self.val)) or (force):
            self.log.debug("\tsend away: {} force={}".format(self.name, force))
            self.updated_value[()].emit()
            
            #print "send display updates", self.name, self.val, self.oldval
            str_val = self.string_value()
            self.updated_value[str].emit(str_val)
            self.updated_text_value.emit(str_val)
                
            if self.dtype in [float, int]:
                #print 'emit', self.name, "updated_value[int]"
                self.updated_value[float].emit(self.val)
                self.updated_value[int].emit(int(self.val))
            self.updated_value[bool].emit(bool(self.val))
            
            if self.choices is not None:
                choice_vals = [c[1] for c in self.choices]
                if self.val in choice_vals:
                    self.updated_choice_index_value.emit(choice_vals.index(self.val) )
            self.oldval = self.val
        else:
            pass
            #print "\t no updates sent", (self.oldval != self.val) , (force), self.oldval, self.val
    
    def same_values(self, v1, v2):
        return v1 == v2
    
    def string_value(self):
        if self.dtype == str:
            return self.val
        else:
            return self.fmt % self.val

    def ini_string_value(self):
        return str(self.val)

    
    def update_choice_index_value(self, new_choice_index, **kwargs):
        self.update_value(self.choices[new_choice_index][1], **kwargs)
    
    def add_listener(self, func, argtype=(), **kwargs):
        """ Connect 'func' as a listener (Qt Slot) for the 
        updated_value signal.
        By default 'func' should take no arguments,
        but argtype can define the data type that it should accept.
        but should be limited to those supported by LoggedQuantity 
        (i.e. int, float, str)
        **kwargs are passed to the connect function
        appends the 'func' to the 'listeners' list
        
        # Wraps func in a try block to absorb the Exception to avoid crashing PyQt5 >5.5
        # see https://riverbankcomputing.com/pipermail/pyqt/2016-March/037134.html
        """
#         def wrapped_func(func):
#             def f(*args):
#                 try:
#                     func(*args)
#                 except Exception as err:
#                     print "Exception on listener:"
        self.updated_value[argtype].connect(func, **kwargs)
        self.listeners.append(func)

    def connect_bidir_to_widget(self, widget):
        """
        Creates Qt signal-slot connections between LQ and the QtWidget *widget*
        
        connects updated_value signal to the appropriate slot depending on 
        the type of widget 
        
        Makes a bidirectional connection to a QT widget, ie when LQ is updated, 
        widget gets a signal and when widget is updated, the LQ receives a signal
        and update_value() slot is called.
        
        Handles many types of widgets:
         * QDoubleSpinBox
         * QCheckBox
         * QLineEdit
         * QComboBox
         * pyqtgraph.widgets.SpinBox.SpinBox        
        
        """
        #print( type(widget) )
        if type(widget) == QtWidgets.QDoubleSpinBox:
            #self.updated_value[float].connect(widget.setValue )
            #widget.valueChanged[float].connect(self.update_value)
            widget.setKeyboardTracking(False)
            if self.vmin is not None:
                widget.setMinimum(self.vmin)
            if self.vmax is not None:
                widget.setMaximum(self.vmax)
            if self.unit is not None:
                widget.setSuffix(" "+self.unit)
            widget.setDecimals(self.spinbox_decimals)
            widget.setSingleStep(self.spinbox_step)
            widget.setValue(self.val)
            #events
            self.updated_value[float].connect(widget.setValue)
            #if not self.ro:
            widget.valueChanged[float].connect(self.update_value)
                
        elif type(widget) == QtWidgets.QCheckBox:
            #print(self.name)
            #self.updated_value[bool].connect(widget.checkStateSet)
            #widget.stateChanged[int].connect(self.update_value)
            # Ed's version
            self.log.debug("connecting checkbox widget")
            self.updated_value[bool].connect(widget.setChecked)
            widget.toggled[bool].connect(self.update_value)
            if self.ro:
                #widget.setReadOnly(True)
                widget.setEnabled(False)
        elif type(widget) == QtWidgets.QLineEdit:
            self.updated_text_value[str].connect(widget.setText)
            if self.ro:
                widget.setReadOnly(True)  # FIXME
            def on_edit_finished():
                self.log.debug("on_edit_finished")
                self.update_value(widget.text())     
            widget.editingFinished.connect(on_edit_finished)
        elif type(widget) == QtWidgets.QPlainTextEdit:
            # FIXME doesn't quite work right: a signal character resets cursor position
            self.updated_text_value[str].connect(widget.setPlainText)
            # TODO Read only
            def set_from_plaintext():
                self.update_value(widget.toPlainText())
            widget.textChanged.connect(set_from_plaintext)
            
        elif type(widget) == QtWidgets.QComboBox:
            # need to have a choice list to connect to a QComboBox
            assert self.choices is not None 
            widget.clear() # removes all old choices
            for choice_name, choice_value in self.choices:
                widget.addItem(choice_name, choice_value)
            self.updated_choice_index_value[int].connect(widget.setCurrentIndex)
            widget.currentIndexChanged.connect(self.update_choice_index_value)
            
        elif type(widget) == pyqtgraph.widgets.SpinBox.SpinBox:
            #widget.setFocusPolicy(QtCore.Qt.StrongFocus)
            suffix = self.unit
            if self.unit is None:
                suffix = ""
            if self.dtype == int:
                integer = True
                minStep=1
                step=1
            else:
                integer = False
                minStep=.1
                step=.1
            widget.setOpts(
                        suffix=suffix,
                        siPrefix=True,
                        dec=True,
                        step=step,
                        minStep=minStep,
                        bounds=[self.vmin, self.vmax],
                        int=integer)            
            if self.ro:
                widget.setEnabled(False)
                widget.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
                widget.setReadOnly(True)
            widget.setDecimals(self.spinbox_decimals)
            widget.setSingleStep(self.spinbox_step)
            self.updated_value[float].connect(widget.setValue)
            #if not self.ro:
                #widget.valueChanged[float].connect(self.update_value)
            widget.valueChanged.connect(self.update_value)
        elif type(widget) == QtWidgets.QLabel:
            self.updated_text_value.connect(widget.setText)
        elif type(widget) == QtWidgets.QProgressBar:
            def set_progressbar(x, widget=widget):
                self.log.debug("set_progressbar {}".format(x))
                widget.setValue(int(x))
            self.updated_value.connect(set_progressbar)
        else:
            raise ValueError("Unknown widget type")
        
        self.send_display_updates(force=True)
        #self.widget = widget
        self.widget_list.append(widget)
        self.change_readonly(self.ro)
    
    def change_choice_list(self, choices):
        #widget = self.widget
        self.choices = self._expand_choices(choices)
        
        for widget in self.widget_list:
            if type(widget) == QtWidgets.QComboBox:
                # need to have a choice list to connect to a QComboBox
                assert self.choices is not None 
                widget.clear() # removes all old choices
                for choice_name, choice_value in self.choices:
                    widget.addItem(choice_name, choice_value)
            else:
                raise RuntimeError("Invalid widget type.")
    
    def change_min_max(self, vmin=-1e12, vmax=+1e12):
        self.vmin = vmin
        self.vmax = vmax
        for widget in self.widget_list: # may not work for certain widget types
            widget.setRange(vmin, vmax)
        self.updated_min_max.emit(vmin,vmax)
        
    def change_readonly(self, ro=True):
        self.ro = ro
        for widget in self.widget_list:
            if type(widget) in [QtWidgets.QDoubleSpinBox, pyqtgraph.widgets.SpinBox.SpinBox]:
                widget.setReadOnly(self.ro)    
            #elif
        self.updated_readonly.emit(self.ro)
        

            

class FileLQ(LoggedQuantity):
    """
    Specialized str type :class:`LoggedQuantity` that handles 
    a filename (or directory) and associated file.
    """
     
    def __init__(self, name, default_dir=None, is_dir=False, **kwargs):
        kwargs.pop('dtype', None)
        
        LoggedQuantity.__init__(self, name, dtype=str, **kwargs)
        
        self.default_dir = default_dir
        self.is_dir = is_dir
        
    def connect_to_browse_widgets(self, lineEdit, pushButton):
        assert type(lineEdit) == QtWidgets.QLineEdit
        self.connect_bidir_to_widget(lineEdit)
    
        assert type(pushButton) == QtWidgets.QPushButton
        pushButton.clicked.connect(self.file_browser)
    
    def file_browser(self):
        # TODO add default directory, etc
        if self.is_dir:
            fname = QtWidgets.QFileDialog.getExistingDirectory(None)
        else:
            fname, _ = QtWidgets.QFileDialog.getOpenFileName(None)
        self.log.debug(repr(fname))
        if fname:
            self.update_value(fname)
            

class ArrayLQ(LoggedQuantity):
    updated_shape = QtCore.Signal(str)
    
    def __init__(self, name, dtype=float, 
                 hardware_read_func=None, hardware_set_func=None, 
                 initial=[], fmt="%g", si=True,
                 ro = False,
                 unit = None,
                 vmin=-1e12, vmax=+1e12, choices=None):
        QtCore.QObject.__init__(self)
        
        self.name = name
        self.dtype = dtype
        self.val = np.array(initial, dtype=dtype)
        self.hardware_read_func = hardware_read_func
        self.hardware_set_func = hardware_set_func
        self.fmt = fmt # % string formatting string. This is ignored if dtype==str
        self.unit = unit
        self.vmin = vmin
        self.vmax = vmax
        self.ro = ro # Read-Only
        
        if self.dtype == int:
            self.spinbox_decimals = 0
        else:
            self.spinbox_decimals = 2
        self.reread_from_hardware_after_write = False
        
        self.oldval = None
        
        self._in_reread_loop = False # flag to prevent reread from hardware loops
        
        self.widget_list = []

    def same_values(self, v1, v2):
        if v1.shape == v2.shape:
            return np.all(v1 == v2)
            self.log.debug("same_values %s %s" % (v2-v1, np.all(v1 == v2)))        
        else:
            return False
            



    def change_shape(self, newshape):
        #TODO
        pass
 
    def string_value (self):
        return json.dumps(self.val.tolist())
    
    def ini_string_value(self):
        return json.dumps(self.val.tolist())
    
    def coerce_to_type(self, x):
        #print type(x)
        if type(x) in (unicode, str):
            x = json.loads(x)
            #print repr(x)
        return np.array(x, dtype=self.dtype)
    
    def send_display_updates(self, force=False):
        self.log.debug(self.name + ' send_display_updates')
        #print "send_display_updates: {} force={}".format(self.name, force)
        if force or np.any(self.oldval != self.val):
            
            #print "send display updates", self.name, self.val, self.oldval
            str_val = self.string_value()
            self.updated_value[str].emit(str_val)
            self.updated_text_value.emit(str_val)
                
            #self.updated_value[float].emit(self.val)
            #if self.dtype != float:
            #    self.updated_value[int].emit(self.val)
            #self.updated_value[bool].emit(self.val)
            self.updated_value[()].emit()
            
            self.oldval = self.val
        else:
            pass
            #print "\t no updates sent", (self.oldval != self.val) , (force), self.oldval, self.val
    

class LQRange(QtCore.QObject):
    """
    LQRange is a collection of logged quantities that describe a
    numpy.linspace array inputs
    Four LQ's are defined, min, max, num, step
    and are connected by signals/slots that keep the quantities
    in sync.
    LQRange.array is the linspace array and is kept upto date
    with changes to the 4 LQ's
    """
    updated_range = QtCore.Signal((),)# (float,),(int,),(bool,), (), (str,),) # signal sent when value has been updated
    
    def __init__(self, min_lq,max_lq,step_lq, num_lq, center_lq=None, span_lq=None):
        QtCore.QObject.__init__(self)
        self.log = get_logger_from_class(self)

        self.min = min_lq
        self.max = max_lq
        self.num = num_lq
        self.step = step_lq
        self.center = center_lq
        self.span = span_lq
        
        assert self.num.dtype == int
        
        self._array_valid = False # Internal _array invalid, must be computed on next request
        
        self._array = None #np.linspace(self.min.val, self.max.val, self.num.val)
        
        #step = self._array[1]-self._array[0]
        step = self.compute_step(self.min.val, self.max.val, self.num.val)
        self.step.update_value(step)
        
        self.num.updated_value[int].connect(self.recalc_with_new_num)
        self.min.updated_value.connect(self.recalc_with_new_min_max)
        self.max.updated_value.connect(self.recalc_with_new_min_max)
        self.step.updated_value.connect(self.recalc_with_new_step)
        
        if self.center and self.span:
            self.center.updated_value.connect(self.recalc_with_new_center_span)
            self.span.updated_value.connect(self.recalc_with_new_center_span)


    @property
    def array(self):
        if self._array_valid:
            return self._array
        else:
            self._array = np.linspace(self.min.val, self.max.val, self.num.val)
            self._array_valid = True
            return self._array

    def compute_step(self, xmin, xmax, num):
        delta = xmax - xmin
        if num > 1:
            return delta/(num-1)
        else:
            return delta

    def recalc_with_new_num(self, new_num):
        self.log.debug("recalc_with_new_num {}".format( new_num))
        self._array_valid = False
        self._array = None
        #self._array = np.linspace(self.min.val, self.max.val, int(new_num))
        new_step = self.compute_step(self.min.val, self.max.val, int(new_num))
        self.log.debug( "    new_step inside new_num {}".format( new_step))
        self.step.update_value(new_step)#, send_signal=True, update_hardware=False)
        self.step.send_display_updates(force=True)
        self.updated_range.emit()
        
    def recalc_with_new_min_max(self, x):
        self._array_valid = False
        self._array = None
        #self._array = np.linspace(self.min.val, self.max.val, self.num.val)
        #step = self._array[1]-self._array[0]
        step = self.compute_step(self.min.val, self.max.val, self.num.val)
        self.step.update_value(step)#, send_signal=True, update_hardware=False)
        if self.center:
            self.span.update_value(0.5*(self.max.val-self.min.val) + self.min.val)
        if self.span:
            self.span.update_value(self.max.val-self.min.val)
        self.updated_range.emit()
        
    def recalc_with_new_step(self,new_step):
        #print "-->recalc_with_new_step"
        if self.num.val > 1:
            #old_step = self._array[1]-self._array[0]
            old_step = self.compute_step(self.min.val, self.max.val, self.num.val)
        else:
            old_step = np.nan
        sdiff = np.abs(old_step - new_step)
        #print "step diff", sdiff
        if sdiff < 10**(-self.step.spinbox_decimals):
            #print "steps close enough, no more recalc"
            return
        else:
            self._array_valid = False
            self._array = None
            new_num = int((((self.max.val - self.min.val)/new_step)+1))
            #self._array = np.linspace(self.min.val, self.max.val, new_num)
            #new_step1 = self._array[1]-self._array[0]
            new_step1 = self.compute_step(self.min.val, self.max.val, new_num)
            
            #print "recalc_with_new_step", new_step, new_num, new_step1
            #self.step.val = new_step1
            #self.num.val = new_num
            #self.step.update_value(new_step1, send_signal=False)
            #if np.abs(self.step.val - new_step1)/self.step.val > 1e-2:
            self.step.val = new_step1
            self.num.update_value(new_num)
            #self.num.send_display_updates(force=True)
            #self.step.update_value(new_step1)

            #print "sending step display Updates"
            #self.step.send_display_updates(force=True)
            self.updated_range.emit()
            
    def recalc_with_new_center_span(self,x):
        C = self.center.val
        S = self.span.val
        self.min.updated_value( C - 0.5*S)
        self.max.updated_value( C + 0.5*S)

class LQCollection(object):
    """
    LQCollection is a smart dictionary of LoggedQuantity objects.
    
    attribute access such as lqcoll.x1 will return full LoggedQuantity object
    
    dictionary-style access lqcoll['x1'] allows direct reading and writing of 
    the LQ's value, while handling the signals
    
    New LQ's can be created with :meth:`New`
    
    LQRange objects can be created with :meth:`New_Range` and will be stored
    in :attr:ranges
    
    """

    def __init__(self):
        self._logged_quantities = OrderedDict()
        self.ranges = OrderedDict()
        
        self.log = get_logger_from_class(self)
        
    def New(self, name, dtype=float, **kwargs):
        is_array = kwargs.pop('array', False)
        #self.log.debug("{} is_array? {}".format(name, is_array))
        if is_array:
            lq = ArrayLQ(name=name, dtype=dtype, **kwargs)
        else:
            if dtype == 'file':
                lq = FileLQ(name=name, **kwargs)
            else:
                lq = LoggedQuantity(name=name, dtype=dtype, **kwargs)
        self._logged_quantities[name] = lq
        self.__dict__[name] = lq
        return lq

    def get_lq(self, key):
        return self._logged_quantities[key]
    
    def get_val(self, key):
        return self._logged_quantities[key].val
    
    def as_list(self):
        return self._logged_quantities.values()
    
    def as_dict(self):
        return self._logged_quantities
    
    def items(self):
        return self._logged_quantities.items()
    
    def __getitem__(self, key):
        "Dictionary-like access reads and sets value of LQ's"
        return self._logged_quantities[key].val
    
    def __setitem__(self, key, item):
        "Dictionary-like access reads and sets value of LQ's"
        self._logged_quantities[key].update_value(item)

    def __contains__(self, key):
        return self._logged_quantities.__contains__(key)
    """
    def __getattribute__(self,name):
        if name in self.logged_quantities.keys():
            return self.logged_quantities[name]
        else:
            return object.__getattribute__(self, name)
    """
    
    def New_Range(self, name, **kwargs):
                        
        min_lq  = self.New( name + "_min" , **kwargs ) 
        max_lq  = self.New( name + "_max" , **kwargs ) 
        step_lq = self.New( name + "_step", **kwargs)
        num_lq  = self.New( name + "_num", dtype=int, vmin=1)
        center_lq = self.New(name + "_center", **kwargs)
        span_lq = self.New( name + "_span", **kwargs)
    
        lqrange = LQRange(min_lq, max_lq, step_lq, num_lq, center_lq, span_lq)

        self.ranges[name] = lqrange
        return lqrange
    
    def New_UI(self):
        """create a default Qt Widget that contains 
        widgets for all settings in the LQCollection
        """
        import pyqtgraph as pg
        
        
        ui_widget =  QtWidgets.QWidget()
        formLayout = QtWidgets.QFormLayout()
        ui_widget.setLayout(formLayout)
        
        for lqname, lq in self.as_dict().items():
            #: :type lq: LoggedQuantity
            if lq.choices is not None:
                widget = QtWidgets.QComboBox()
            elif lq.dtype in [int, float]:
                if lq.si:
                    widget = pg.SpinBox()
                else:
                    widget = QtWidgets.QDoubleSpinBox()
            elif lq.dtype in [bool]:
                widget = QtWidgets.QCheckBox()  
            elif lq.dtype in [str]:
                widget = QtWidgets.QLineEdit()
            lq.connect_bidir_to_widget(widget)

            # Add to formlayout
            formLayout.addRow(lqname, widget)
            #lq_tree_item = QtWidgets.QTreeWidgetItem(self.tree_item, [lqname, ""])
            #self.tree_item.addChild(lq_tree_item)
            #lq.hardware_tree_widget = widget
            #tree.setItemWidget(lq_tree_item, 1, lq.hardware_tree_widget)
            #self.control_widgets[lqname] = widget  
        return ui_widget

def print_signals_and_slots(obj):
    # http://visitusers.org/index.php?title=PySide_Recipes
    for i in range(obj.metaObject().methodCount()):
        m = obj.metaObject().method(i)
        if m.methodType() == QtCore.QMetaMethod.MethodType.Signal:
            print("SIGNAL: sig=", m.signature(), "hooked to nslots=",obj.receivers(QtCore.SIGNAL(m.signature())))
        elif m.methodType() == QtCore.QMetaMethod.MethodType.Slot:
            print("SLOT: sig=", m.signature())