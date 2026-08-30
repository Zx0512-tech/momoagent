from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


SpectrumModel = Literal['davenport', 'kaimal']
VerticalSpectrumModel = Literal['panofsky', 'lumley_panofsky']
SimulationMethod = Literal['harmonic', 'ar']
LoadModel = Literal['linearized', 'full_velocity_squared']


class WindRequestModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra='forbid',
        strict=True,
    )


class ProjectInput(WindRequestModel):
    name: str = Field(min_length=1)
    note: str = ''


class SiteWindInput(WindRequestModel):

    u10: float = Field(gt=0)
    risk_coefficient: float = Field(alias='riskCoefficient', gt=0)
    surface_class: str = Field(alias='surfaceClass', min_length=1)
    terrain_coefficient: float = Field(alias='terrainCoefficient', gt=0)
    air_density: float = Field(alias='airDensity', gt=0)
    girder_reference_height: float = Field(alias='girderReferenceHeight', gt=0)
    tower_heights: list[float] = Field(alias='towerHeights', min_length=2)
    enforce_code_minimum_wind_speed: bool = Field(default=True, alias='enforceCodeMinimumWindSpeed')


class GirderInput(WindRequestModel):

    length: float = Field(gt=0)
    segment_count: int = Field(alias='segmentCount', ge=2)
    width: float = Field(gt=0)
    depth: float = Field(gt=0)
    ch: float
    cv: float
    cm: float


class TowerInput(WindRequestModel):

    height: float = Field(gt=0)
    segment_count: int = Field(alias='segmentCount', ge=2)
    width: float = Field(gt=0)
    cd: float


class TimeHistoryInput(WindRequestModel):

    duration: float = Field(gt=0)
    time_step: float = Field(alias='timeStep', gt=0)
    frequency_count: int = Field(alias='frequencyCount', ge=16)
    seed: int
    spectrum_model: SpectrumModel = Field(alias='spectrumModel', default='davenport')
    vertical_spectrum_model: VerticalSpectrumModel = Field(alias='verticalSpectrumModel', default='panofsky')
    simulation_method: SimulationMethod = Field(alias='simulationMethod', default='harmonic')
    ar_order: int = Field(alias='arOrder', ge=1, le=12, default=4)
    load_model: LoadModel = Field(alias='loadModel', default='linearized')


class OverrideInput(WindRequestModel):
    enabled: bool = False
    k1: float | None = None
    iu: float | None = None
    iv: float | None = None
    iw: float | None = None


class WindRequest(WindRequestModel):
    project: ProjectInput
    site: SiteWindInput
    girder: GirderInput
    tower: TowerInput
    time_history: TimeHistoryInput
    overrides: OverrideInput
