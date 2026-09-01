"""ANSYS damper APDL and metadata helpers."""

from __future__ import annotations

from pyansys_bridge.models import DamperParams, RealizableDamper

ANSYS_DAMPER_C_SCALE = 1000.0
DEFAULT_DAMPER_REGULARIZATION_VELOCITY = 1.0e-4
ANSYS_USER300_VISCOUS_MODULE = "damper_user300_viscous"
ANSYS_USER300_MODULE_TYPES = {
    "damper_eddy_current": 1,
    "damper_friction": 2,
    ANSYS_USER300_VISCOUS_MODULE: 3,
}


def ansys_damper_commands(
    damper_module: str,
    dampers: tuple[RealizableDamper, ...],
    c_scale: float = ANSYS_DAMPER_C_SCALE,
) -> str:
    lines = ["/PREP7", _damper_export_header(damper_module)]
    if any(uses_userrc_regularization(damper_module, damper.params) for damper in dampers):
        lines.extend(
            [
                "! Compile and load the COMBIN37 USERRC routine for regularized damping",
                "/UPF,userrc_regularized_viscous.f",
            ]
        )
    for index, damper in enumerate(dampers, start=1):
        element_type = 2000 + index
        element_id = 3000 + index
        real_constant_id = 4000 + index
        lines.extend(_ansys_damper_block(damper_module, damper, element_type, element_id, real_constant_id, c_scale))
    return "\n".join(lines)


def ansys_damper_params_contract(
    params: DamperParams,
    c_scale: float = ANSYS_DAMPER_C_SCALE,
    physical_count_per_tower: int = 1,
    realizable_damper_count: int | None = None,
    damper_module: str = "damper_viscous",
) -> dict[str, object]:
    regularized = uses_userrc_regularization(damper_module, params)
    user300 = uses_user300(damper_module)
    exported_primary = _ansys_exported_primary(damper_module, params.c, c_scale)
    contract = {
        "c_total_per_tower": float(params.c),
        "ansys_c_scale": float(c_scale) if uses_user300_viscous(damper_module) or damper_module == "damper_viscous" else 1.0,
        "exported_c_total_per_tower": exported_primary,
        "alpha": float(params.alpha),
        "implemented_exponent": _implemented_exponent(params.alpha),
        "stiffness": 0.0,
        "implementation": _damper_implementation_name(damper_module, regularized),
        "damper_count_per_tower": int(physical_count_per_tower),
        "realizable_damper_count": realizable_damper_count,
        "split_physical_dampers": int(physical_count_per_tower) > 1,
    }
    if regularized or user300:
        contract.update(
            {
                "regularized": True,
                "regularization_velocity": _regularization_velocity(params),
                "regularized_exponent": _regularized_exponent(params.alpha),
            }
        )
    if regularized:
        contract.update({"requires_user_subroutine": True, "user_subroutine": "USERRC"})
    if user300:
        contract.update(
            {
                "requires_user_element": True,
                "user_element": "USER300",
                "user300_type": _user300_type_key(damper_module),
                "tangent_mode": _user300_tangent_mode(damper_module),
            }
        )
    return contract


def ansys_damper_elements_contract(
    dampers: tuple[RealizableDamper, ...],
    c_scale: float = ANSYS_DAMPER_C_SCALE,
    damper_module: str = "damper_viscous",
) -> list[dict[str, object]]:
    elements = []
    for index, damper in enumerate(dampers, start=1):
        implemented_exponent = _implemented_exponent(damper.params.alpha)
        exported_c = _ansys_exported_primary(damper_module, damper.params.c, c_scale)
        regularized = uses_userrc_regularization(damper_module, damper.params)
        user300 = uses_user300(damper_module)
        element = {
            "name": damper.placement_name,
            "unit_index": damper.unit_index,
            "element_id": 3000 + index,
            "element_type_id": 2000 + index,
            "real_constant_id": 4000 + index,
            "node_i": damper.node_i,
            "node_j": damper.node_j,
            "direction": damper.direction,
            "c_input": float(damper.params.c),
            "ansys_c_scale": float(c_scale),
            "exported_c": exported_c,
            "alpha": float(damper.params.alpha),
            "exported_exponent": implemented_exponent,
            "stiffness": float(damper.params.stiffness or 0.0),
        }
        if regularized or user300:
            element.update(
                {
                    "regularized": True,
                    "regularization_velocity": _regularization_velocity(damper.params),
                    "regularized_exponent": _regularized_exponent(damper.params.alpha),
                }
            )
        if user300:
            element.update(
                {
                    "user_element": "USER300",
                    "user300_type": _user300_type_key(damper_module),
                    "active_dir": _ansys_damper_dof(damper.direction),
                    "tangent_mode": _user300_tangent_mode(damper_module),
                }
            )
        elements.append(element)
    return elements


def ansys_userrc_source() -> str:
    return "\n".join(
        [
            "*deck,userrc       USERDISTRIB  parallel                                pck",
            "      subroutine userrc (elem,ireal,type,nusvr,usvr,parm,parmld,",
            "     x c1,c2,c3,c4,fcon)",
            "c     primary function: user operation on parameter for combin37",
            "c      accessed with keyopt(9) = 1",
            "c",
            "c     Regularized nonlinear viscous damper for COMBIN37.",
            "c",
            "c     Physical model:",
            "c       F = C*abs(v)**alpha*sign(v)",
            "c",
            "c     USERRC returns the equivalent damping coefficient:",
            "c       fcon = C*(v*v + v0*v0)**((alpha-1)/2)",
            "c       F = fcon*v",
            "c",
            "c     Real constants written by the APDL generator:",
            "c       c1 = exported damper coefficient C",
            "c       c2 = physical velocity exponent alpha",
            "c       c3 = regularization velocity v0",
            "c       c4 = unused",
            "c",
            '#include "impcom.inc"',
            "",
            "      integer elem,ireal,type,nusvr",
            "      double precision",
            "     x usvr(*),parm,parmld,c1,c2,c3,c4,fcon",
            "      double precision v0,exponent",
            "",
            "      v0 = c3",
            "      if (v0 .le. 0.0d0) v0 = 1.0d-12",
            "      exponent = (c2 - 1.0d0)/2.0d0",
            "",
            "      if (c1 .le. 0.0d0) then",
            "         fcon = 0.0d0",
            "      else",
            "         fcon = c1*(parm*parm + v0*v0)**exponent",
            "      endif",
            "",
            "      if (elem .lt. 0 .or. ireal .lt. 0 .or. type .lt. 0",
            "     x    .or. nusvr .lt. 0 .or. parmld .ne. parmld",
            "     x    .or. c4 .ne. c4) then",
            "         fcon = fcon",
            "      endif",
            "",
            "      return",
            "      end",
            "",
        ]
    )


def uses_userrc_regularization(damper_module: str, params: DamperParams) -> bool:
    return damper_module == "damper_viscous" and float(params.alpha) < 1.0


def uses_user300_viscous(damper_module: str) -> bool:
    return damper_module == ANSYS_USER300_VISCOUS_MODULE


def uses_user300(damper_module: str) -> bool:
    return damper_module in ANSYS_USER300_MODULE_TYPES


def _ansys_element_type(damper_module: str) -> int:
    mapping = {
        "damper_viscous": 37,
        ANSYS_USER300_VISCOUS_MODULE: "USER300",
        "damper_friction": "USER300",
        "damper_eddy_current": "USER300",
    }
    if damper_module not in mapping:
        raise ValueError(f"Unsupported ANSYS damper module: {damper_module}")
    return mapping[damper_module]


def _ansys_damper_block(
    damper_module: str,
    damper: RealizableDamper,
    element_type: int,
    element_id: int,
    real_constant_id: int,
    c_scale: float = ANSYS_DAMPER_C_SCALE,
) -> list[str]:
    exported_c = _ansys_exported_primary(damper_module, damper.params.c, c_scale)
    if damper_module == "damper_eddy_current":
        formula = "F = 2*FMAX*VCR*v/(v*v+VCR*VCR)"
        parameter_note = f"FMAX={damper.params.c}; VCR={damper.params.alpha}"
    elif damper_module == "damper_friction":
        formula = "F = FC*tanh(v/VS)"
        parameter_note = f"FC={damper.params.c}; VS={damper.params.alpha}"
    else:
        formula = "F = C*abs(v)^alpha*sign(v)"
        parameter_note = (
            f"input_C={damper.params.c}; ansys_C={exported_c}; alpha={damper.params.alpha}"
        )
    header = [
        (
            f"! nonlinear viscous damper: {damper.placement_name}"
            if damper_module in {"damper_viscous", ANSYS_USER300_VISCOUS_MODULE}
            else f"! damper: {damper.placement_name}"
        ),
        f"! physical model: {formula}",
        _damper_formula_note(damper_module),
        f"! nodes={damper.node_i},{damper.node_j}; {parameter_note}",
        f"ET,{element_type},{_ansys_element_type(damper_module)}",
    ]
    if uses_user300(damper_module):
        dof = _ansys_damper_dof(damper.direction)
        type_key = _user300_type_key(damper_module)
        tangent_mode = _user300_tangent_mode(damper_module)
        if uses_user300_viscous(damper_module):
            real_constants = (
                f"{exported_c},{float(damper.params.alpha)},"
                f"{_regularization_velocity(damper.params)},{dof},1"
            )
        else:
            real_constants = f"{float(damper.params.c)},{float(damper.params.alpha)},0,{dof},1"
        return [
            *header,
            f"! USER300 implementation: type={type_key}; tangent_mode={tangent_mode}",
            f"! active_dir={dof}; active_flag=1",
            f"KEYOPT,{element_type},1,{type_key}",
            f"KEYOPT,{element_type},2,{tangent_mode}",
            f"TYPE,{element_type}",
            "USRELEM,2,3,LINE,5,5,5,0,0,0,0",
            "USRDOF,,UX,UY,UZ",
            f"R,{real_constant_id},{real_constants}",
            f"TYPE,{element_type}",
            f"REAL,{real_constant_id}",
            f"EN,{element_id},{damper.node_i},{damper.node_j}",
        ]
    if damper_module == "damper_viscous":
        dof = _ansys_damper_dof(damper.direction)
        implemented_exponent = _implemented_exponent(damper.params.alpha)
        if uses_userrc_regularization(damper_module, damper.params):
            regularization_velocity = _regularization_velocity(damper.params)
            return [
                *header,
                "! COMBIN37 implementation: USERRC returns regularized DAMP",
                "! v = CONTROL PARAM",
                "! USERRC regularization: Dmod = C*(v*v+v0*v0)^((alpha-1)/2)",
                "! F = Dmod*v",
                f"! v0={regularization_velocity}; regularized_exponent="
                f"{_regularized_exponent(damper.params.alpha)}",
                "! USERRC source file: userrc_regularized_viscous.f",
                f"KEYOPT,{element_type},1,2",
                f"KEYOPT,{element_type},2,{dof}",
                f"KEYOPT,{element_type},3,{dof}",
                f"KEYOPT,{element_type},6,2",
                f"KEYOPT,{element_type},9,1",
                f"TYPE,{element_type}",
                f"REAL,{real_constant_id}",
                f"R,{real_constant_id},0,0,0,0,0,0",
                f"RMORE,0,1,{exported_c},{float(damper.params.alpha)},{regularization_velocity},0",
                f"EN,{element_id},{damper.node_i},{damper.node_j},{damper.node_i},{damper.node_j}",
            ]
        return [
            *header,
            f"KEYOPT,{element_type},1,2",
            f"KEYOPT,{element_type},2,{dof}",
            f"KEYOPT,{element_type},3,{dof}",
            f"KEYOPT,{element_type},6,2",
            f"KEYOPT,{element_type},9,0",
            f"TYPE,{element_type}",
            f"REAL,{real_constant_id}",
            f"R,{real_constant_id},0,0,0,0,0,0",
            f"RMORE,0,1,{exported_c},{implemented_exponent},0,0",
            f"EN,{element_id},{damper.node_i},{damper.node_j},{damper.node_i},{damper.node_j}",
        ]
    return [
        *header,
        f"TYPE,{element_type}",
        f"REAL,{real_constant_id}",
        f"R,{real_constant_id},{damper.params.stiffness or 0.0},{damper.params.c}",
        f"EN,{element_id},{damper.node_i},{damper.node_j}",
    ]


def _ansys_damper_dof(direction: str) -> int:
    mapping = {"X": 1, "Y": 2, "Z": 3, "ROTX": 4, "ROTY": 5, "ROTZ": 6}
    return mapping.get(direction.upper(), 1)


def _damper_export_header(damper_module: str) -> str:
    if uses_user300(damper_module):
        return "! Tower-girder dampers exported as explicit physical USER300 units"
    return "! Tower-girder dampers exported as explicit physical COMBIN37 units"


def _damper_formula_note(damper_module: str) -> str:
    if uses_user300_viscous(damper_module):
        return "! USER300 implementation: F = C*max(abs(v),VFLOOR)^(alpha-1)*v"
    if damper_module == "damper_eddy_current":
        return "! USER300 eddy-current implementation"
    if damper_module == "damper_friction":
        return "! USER300 friction implementation"
    return "! COMBIN37 implementation: F = Ceq(v)*v; Ceq(v) = C*abs(v)^(alpha-1)"


def _damper_implementation_name(damper_module: str, regularized: bool) -> str:
    if uses_user300_viscous(damper_module):
        return "USER300_VISCOUS_DAMPER"
    if damper_module == "damper_eddy_current":
        return "USER300_EDDY_CURRENT_DAMPER"
    if damper_module == "damper_friction":
        return "USER300_FRICTION_DAMPER"
    return "COMBIN37_USERRC_REGULARIZED_DAMP_MODIFICATION" if regularized else "COMBIN37_DAMP_MODIFICATION"


def _implemented_exponent(alpha: float) -> float:
    return float(alpha) - 1.0


def _regularized_exponent(alpha: float) -> float:
    return (float(alpha) - 1.0) / 2.0


def _regularization_velocity(params: DamperParams) -> float:
    if params.regularization_velocity is not None:
        return float(params.regularization_velocity)
    return DEFAULT_DAMPER_REGULARIZATION_VELOCITY


def _ansys_exported_c(input_c: float, c_scale: float = ANSYS_DAMPER_C_SCALE) -> float:
    return float(input_c) * float(c_scale)


def _ansys_exported_primary(damper_module: str, value: float, c_scale: float) -> float:
    if damper_module in {"damper_viscous", ANSYS_USER300_VISCOUS_MODULE}:
        return _ansys_exported_c(value, c_scale)
    return float(value)


def _user300_type_key(damper_module: str) -> int:
    try:
        return ANSYS_USER300_MODULE_TYPES[damper_module]
    except KeyError as exc:
        raise ValueError(f"Unsupported USER300 damper module: {damper_module}") from exc


def _user300_tangent_mode(damper_module: str) -> int:
    return 6 if uses_user300_viscous(damper_module) else 1
