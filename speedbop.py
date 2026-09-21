
import dataclasses
from dataclasses import dataclass, asdict, fields
import json
import math
import pathlib

# https://stackoverflow.com/a/54769644
def _dataclass_from_dict(klass, d):
  if isinstance(d, list):
    (inner,) = klass.__args__
    return [_dataclass_from_dict(inner, i) for i in data]

  try:
    fieldtypes = {f.name:f.type for f in dataclasses.fields(klass)}
    return klass(**{f:_dataclass_from_dict(fieldtypes[f],d[f]) for f in d})
  except:
    return d # Not a dataclass field
  
  
def _bop_tablerow_lookup(value: float, table: dict[str, tuple[any]]) -> tuple[any]:
  # We need to check for str here, as json only accepts strings, not numbers as
  # dict keys.
  all_keys = sorted(table.keys())
  for key in all_keys:
    val = float(key)
    if val >= value:
      return table[key]     
  return table[all_keys[-1]]


@dataclass(frozen=True)
class AircraftDataCard:
  # Represents a single type of airplane. All of these fields are constant.
  name: str
  version: str
  
  @dataclass(frozen=True)
  class Characteristics:
    wing_area:  float

    # In loads; i.e. 1/3 g's
    combat_safe_load: float   
  
  @dataclass(frozen=True)
  class Stores:
    combat_weight: float


  @dataclass(frozen=True)
  class Lift:
    alpha_max: float
    
    mach_lcs_ids_table: dict[float, tuple[float, int]]
    

  lift:             Lift
  characteristics:  Characteristics
  stores:           Stores
   
  @classmethod
  def from_json(cls, path: pathlib.Path) -> 'AircraftDataCard':
    with open(path, 'r') as file:      
      adc_dict = json.loads(file.read())
      return _dataclass_from_dict(AircraftDataCard, adc_dict)


@dataclass
class AircraftState:
  adc:    AircraftDataCard
  
  # From scenario.
  weight: float
  
  # From scenario / last turn.
  ktas: float
  altitude: int
  
          
  def get_wing_load(self) -> float:
    return round(self.weight / self.adc.characteristics.wing_area * 10.0, 1)
  
  def get_safe_load(self) -> float:
    safe_load = self.adc.stores.combat_weight / self.weight * self.adc.characteristics.combat_safe_load
    return round(safe_load, 1)

  def get_keas(self) -> float:
    return round(self.ktas / math.exp(0.003358 * self.altitude))
    
  def get_q(self) -> float:
    keas = self.get_keas()
    # q = keas² / 2950 (or keas² * 0.000339); empirically determined.
    return round(keas**2 / 2950, 1)
  
  def get_smash(self) -> float:
    return round(10.0 * self.get_q() / self.get_wing_load(), 1)

  def get_mach(self) -> float:
    keas = self.get_keas()
    # keas = 674.573 * mach * exp(-0.0044558 * altitude); empirically determined.
    return round(keas * math.exp(0.0044558 * self.altitude) / 674.573, 1)

  def get_mach(self) -> float:
    keas = self.get_keas()
    mach = keas * math.exp(0.0045*self.altitude) / 674.6
    return round(mach, 1)

  def get_lcs(self) -> float:
    mach = self.get_mach()
    lcs = _bop_tablerow_lookup(mach, 
                               self.adc.lift.mach_lcs_ids_table)
    return lcs[0]
  
  def get_max_load(self) -> int:
    max_load = self.adc.lift.alpha_max / self.get_lcs() * self.get_smash()
    # We don't want to exceed our max load ever, hence we round down.
    return math.floor(max_load)
  
  
  def calculate_corner_speed(self) -> float:
    alpha_over_lcs = self.adc.lift.alpha_max / self.get_lcs()
    desired_smash = self.adc.characteristics.combat_safe_load / alpha_over_lcs
    desired_q = q_from_smash(desired_smash, self.get_wing_load())
    desired_keas = keas_from_q(desired_q)
    return math.floor(desired_keas)


def speed_fp_from_ktas(ktas: float) -> int:
  if ktas < 60:
    return 1
  return 2 + (ktas - 60) // 40 

# Inverse helpers -- going from a derived value back to the inputs that
# would have produced it, e.g. when a known smash/q/mach reading needs to
# be converted back into ktas for the next turn's calculation.

def q_from_smash(smash: float, wl: float) -> float:
  """Inverse of AircraftState.get_smash(): smash = 10 * q / wl."""
  return smash * wl / 10.0

def keas_from_q(q: float) -> float:
  """Inverse of AircraftState.get_q(): q = keas^2 / 2950."""
  return math.sqrt(q * 2950)

def ktas_from_keas(keas: float, altitude: float) -> float:
  """Inverse of AircraftState.get_keas(): keas = ktas / exp(0.003358*altitude)."""
  return keas * math.exp(0.003358 * altitude)

def ktas_from_q(q: float, altitude: float) -> float:
  """Chains keas_from_q and ktas_from_keas."""
  return ktas_from_keas(keas_from_q(q), altitude)


def main() -> None:
  adc = AircraftDataCard.from_json(pathlib.Path("adc/fj-3m.json")) 
  state = AircraftState(adc, weight=17.4, ktas=485, altitude=75)
    
  
  print('Wing load: ', state.get_wing_load())
  print('Safe load: ', state.get_safe_load())
  print('KTAS:', state.ktas, speed_fp_from_ktas(state.ktas))
  print('KEAS:', state.get_keas())
  print('Q:', state.get_q())
  print('Smash:', state.get_smash())
  print('Mach:', state.get_mach())
  print('LCS:', state.get_lcs())
  print('Max load:', state.get_max_load())
  print('Corner speed:', state.calculate_corner_speed())


if __name__ == '__main__':
  main()