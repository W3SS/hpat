import numba
import hpat
from numba import types
from numba.typing.templates import infer_global, AbstractTemplate, infer, signature, AttributeTemplate, infer_getattr
from numba.extending import (typeof_impl, type_callable, models, register_model,
                                make_attribute_wrapper, lower_builtin, box, lower_getattr)
from numba import cgutils
from hpat.str_ext import string_type
from numba.targets.imputils import impl_ret_new_ref, impl_ret_borrowed

class StringArray(object):
    def __init__(self, offsets, data, size):
        self.size = size
        self.offsets = offsets
        self.data = data

    def __repr__(self):
        return 'StringArray({}, {}, {})'.format(self.offsets, self.data, self.size)

class StringArrayType(types.Type):
    def __init__(self):
        super(StringArrayType, self).__init__(
                                    name='StringArrayType()')

string_array_type = StringArrayType()

@typeof_impl.register(StringArray)
def typeof_string_array(val, c):
    return string_array_type

# @type_callable(StringArray)
# def type_string_array_call(context):
#     def typer(offset, data):
#         return string_array_type
#     return typer

@type_callable(StringArray)
def type_string_array_call2(context):
    def typer(string_list=None):
        return string_array_type
    return typer

class StringArrayPayloadType(types.Type):
    def __init__(self):
        super(StringArrayPayloadType, self).__init__(
                                    name='StringArrayPayloadType()')

@register_model(StringArrayPayloadType)
class StringArrayPayloadModel(models.StructModel):
    def __init__(self, dmm, fe_type):
        members = [
            ('size', types.intp),
            ('offsets', types.Opaque('offsets')),
            ('data', hpat.str_ext.string_type),
            ]
        models.StructModel.__init__(self, dmm, fe_type, members)

@register_model(StringArrayType)
class StringArrayModel(models.StructModel):
    def __init__(self, dmm, fe_type):
        dtype = StringArrayPayloadType()
        members = [
            ('meminfo', types.MemInfoPointer(dtype)),
            ]
        models.StructModel.__init__(self, dmm, fe_type, members)

@infer
class GetItemStringArray(AbstractTemplate):
    key = "getitem"

    def generic(self, args, kws):
        assert not kws
        [ary, idx] = args
        if isinstance(ary, StringArrayType):
            if isinstance(idx, types.SliceType):
                return signature(string_array_type, *args)
            else:
                assert isinstance(idx, types.Integer)
                return signature(string_type, *args)


@infer
class CmpOpEqStringArray(AbstractTemplate):
    key = '=='

    def generic(self, args, kws):
        assert not kws
        [va, vb] = args
        # if one of the inputs is string array
        if va==string_array_type or vb==string_array_type:
            # inputs should be either string array or string
            assert va == string_array_type or va == string_type
            assert vb == string_array_type or vb == string_type
            return signature(types.Array(types.boolean, 1, 'C'), va, vb)

@infer
class CmpOpNEqStringArray(CmpOpEqStringArray):
    key = '!='

# @infer_global(len)
# class LenStringArray(AbstractTemplate):
#     def generic(self, args, kws):
#         if not kws and len(args)==1 and args[0]==string_array_type:
#             return signature(types.intp, *args)

make_attribute_wrapper(StringArrayPayloadType, 'size', 'size')
make_attribute_wrapper(StringArrayPayloadType, 'offsets', 'offsets')
make_attribute_wrapper(StringArrayPayloadType, 'data', 'data')


@infer_getattr
class StrArrayAttribute(AttributeTemplate):
    key = string_array_type

    def resolve_size(self, ctflags):
        return types.intp

@lower_getattr(string_array_type, 'size')
def str_arr_size_impl(context, builder, typ, val):
    dtype = StringArrayPayloadType()
    inst_struct = context.make_helper(builder, typ, val)
    data_pointer = context.nrt.meminfo_data(builder, inst_struct.meminfo)
    # cgutils.printf(builder, "data [%p]\n", data_pointer)
    data_pointer = builder.bitcast(data_pointer,
                                    context.get_data_type(dtype).as_pointer())


    string_array = cgutils.create_struct_proxy(dtype)(context, builder, builder.load(data_pointer))
    attrval = string_array.size
    attrty = types.intp
    return impl_ret_borrowed(context, builder, attrty, attrval)

# @lower_builtin(StringArray, types.Type, types.Type)
# def impl_string_array(context, builder, sig, args):
#     typ = sig.return_type
#     offsets, data = args
#     string_array = cgutils.create_struct_proxy(typ)(context, builder)
#     string_array.offsets = offsets
#     string_array.data = data
#     return string_array._getvalue()

from numba.extending import overload

@overload(len)
def str_arr_len_overload(str_arr):
    if str_arr == string_array_type:
        def str_arr_len(s):
            return s.size
        return str_arr_len

from numba.targets.listobj import ListInstance
from llvmlite import ir as lir
import llvmlite.binding as ll
import hstr_ext
ll.add_symbol('get_str_len', hstr_ext.get_str_len)
ll.add_symbol('allocate_string_array', hstr_ext.allocate_string_array)
ll.add_symbol('setitem_string_array', hstr_ext.setitem_string_array)
ll.add_symbol('getitem_string_array', hstr_ext.getitem_string_array)
ll.add_symbol('getitem_string_array_std', hstr_ext.getitem_string_array_std)
ll.add_symbol('print_int', hstr_ext.print_int)

import hstr_ext
ll.add_symbol('dtor_string_array', hstr_ext.dtor_string_array)

def construct_string_array(context, builder):
    typ = string_array_type
    dtype = StringArrayPayloadType()
    alloc_type = context.get_data_type(dtype)
    alloc_size = context.get_abi_sizeof(alloc_type)

    llvoidptr = context.get_value_type(types.voidptr)
    llsize = context.get_value_type(types.uintp)
    dtor_ftype = lir.FunctionType(lir.VoidType(),
                                     [llvoidptr, llsize, llvoidptr])
    dtor_fn = builder.module.get_or_insert_function(dtor_ftype, name="dtor_string_array")

    meminfo = context.nrt.meminfo_alloc_dtor(
        builder,
        context.get_constant(types.uintp, alloc_size),
        dtor_fn,
    )
    data_pointer = context.nrt.meminfo_data(builder, meminfo)
    data_pointer = builder.bitcast(data_pointer,
                                   alloc_type.as_pointer())

    # Nullify all data
    #builder.store( cgutils.get_null_value(alloc_type),
    #             data_pointer)
    return meminfo, data_pointer


@lower_builtin(StringArray)
@lower_builtin(StringArray, types.List)
def impl_string_array_single(context, builder, sig, args):
    typ = sig.return_type
    dtype = StringArrayPayloadType()
    meminfo, data_pointer = construct_string_array(context, builder)

    string_array = cgutils.create_struct_proxy(dtype)(context, builder)
    if not sig.args:  # return empty string array if no args
        builder.store(string_array._getvalue(),
                      data_pointer)
        inst_struct = context.make_helper(builder, typ)
        inst_struct.meminfo = meminfo
        ret = inst_struct._getvalue()
        #context.nrt.decref(builder, ty, ret)

        return impl_ret_new_ref(context, builder, typ, ret)

    string_list = ListInstance(context, builder, sig.args[0], args[0])

    # get total size of string buffer
    fnty = lir.FunctionType( lir.IntType(64),
                            [lir.IntType(8).as_pointer()])
    fn_len = builder.module.get_or_insert_function(fnty, name="get_str_len")
    zero = context.get_constant(types.intp, 0)
    total_size = cgutils.alloca_once_value(builder, zero)
    string_array.size = string_list.size

    # loop through all strings and get length
    with cgutils.for_range(builder, string_list.size) as loop:
        str_value = string_list.getitem(loop.index)
        str_len = builder.call(fn_len, [str_value])
        builder.store(builder.add(builder.load(total_size), str_len), total_size)

    # allocate string array
    fnty = lir.FunctionType( lir.VoidType(),
                            [lir.IntType(8).as_pointer().as_pointer(),
                            lir.IntType(8).as_pointer().as_pointer(),
                            lir.IntType(64),
                            lir.IntType(64)])
    fn_alloc = builder.module.get_or_insert_function(fnty,
                                                name="allocate_string_array")
    builder.call(fn_alloc, [string_array._get_ptr_by_name('offsets'),
                            string_array._get_ptr_by_name('data'),
                            string_list.size, builder.load(total_size)])

    # set string array values
    fnty = lir.FunctionType( lir.VoidType(),
                            [lir.IntType(8).as_pointer(),
                            lir.IntType(8).as_pointer(),
                            lir.IntType(8).as_pointer(),
                            lir.IntType(64)])
    fn_setitem = builder.module.get_or_insert_function(fnty,
                                                name="setitem_string_array")

    with cgutils.for_range(builder, string_list.size) as loop:
        str_value = string_list.getitem(loop.index)
        builder.call(fn_setitem, [string_array.offsets, string_array.data,
                        str_value, loop.index])

    builder.store(string_array._getvalue(),
                  data_pointer)
    inst_struct = context.make_helper(builder, typ)
    inst_struct.meminfo = meminfo
    ret = inst_struct._getvalue()
    #context.nrt.decref(builder, ty, ret)

    return impl_ret_new_ref(context, builder, typ, ret)

@box(StringArrayType)
def box_str(typ, val, c):
    """
    """
    dtype = StringArrayPayloadType()

    inst_struct = c.context.make_helper(c.builder, typ, val)
    data_pointer = c.context.nrt.meminfo_data(c.builder, inst_struct.meminfo)
    # cgutils.printf(builder, "data [%p]\n", data_pointer)
    data_pointer = c.builder.bitcast(data_pointer,
                                    c.context.get_data_type(dtype).as_pointer())


    string_array = cgutils.create_struct_proxy(dtype)(c.context, c.builder, c.builder.load(data_pointer))

    # fnty = lir.FunctionType(lir.VoidType(), [lir.IntType(64)])
    # fn_print_int = c.builder.module.get_or_insert_function(fnty,
    #                                             name="print_int")
    # c.builder.call(fn_print_int, [string_array.size])

    string_list = c.pyapi.list_new(string_array.size)
    res = cgutils.alloca_once(c.builder, lir.IntType(8).as_pointer())
    c.builder.store(string_list, res)

    fnty = lir.FunctionType( lir.IntType(8).as_pointer(),
                            [lir.IntType(8).as_pointer(),
                            lir.IntType(8).as_pointer(),
                            lir.IntType(64)])
    fn_getitem = c.builder.module.get_or_insert_function(fnty,
                                                name="getitem_string_array")

    with cgutils.for_range(c.builder, string_array.size) as loop:
        c_str = c.builder.call(fn_getitem, [string_array.offsets,
                                    string_array.data, loop.index])
        pystr = c.pyapi.string_from_string(c_str)
        c.pyapi.list_setitem(string_list, loop.index, pystr)

    c.context.nrt.decref(c.builder, typ, val)
    return c.builder.load(res)


@lower_builtin('getitem', StringArrayType, types.Integer)
def lower_string_arr_getitem(context, builder, sig, args):
    typ = sig.args[0]
    dtype = StringArrayPayloadType()

    inst_struct = context.make_helper(builder, typ, args[0])
    data_pointer = context.nrt.meminfo_data(builder, inst_struct.meminfo)
    # cgutils.printf(builder, "data [%p]\n", data_pointer)
    data_pointer = builder.bitcast(data_pointer,
                                    context.get_data_type(dtype).as_pointer())


    string_array = cgutils.create_struct_proxy(dtype)(context, builder, builder.load(data_pointer))
    fnty = lir.FunctionType( lir.IntType(8).as_pointer(),
                            [lir.IntType(8).as_pointer(),
                            lir.IntType(8).as_pointer(),
                            lir.IntType(64)])
    fn_getitem = builder.module.get_or_insert_function(fnty,
                                                name="getitem_string_array_std")
    return builder.call(fn_getitem, [string_array.offsets,
                                string_array.data, args[1]])
