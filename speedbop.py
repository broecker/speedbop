
import dataclasses
from dataclasses import dataclass, asdict, fields
import json
import pathlib

# https://stackoverflow.com/a/54769644
def dataclass_from_dict(klass, d):
  if isinstance(d, list):
    (inner,) = klass.__args__
    return [dataclass_from_dict(inner, i) for i in data]

  try:
    fieldtypes = {f.name:f.type for f in dataclasses.fields(klass)}
    return klass(**{f:dataclass_from_dict(fieldtypes[f],d[f]) for f in d})
  except:
    return d # Not a dataclass field

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


  characteristics:  Characteristics
  stores:           Stores
  
  @classmethod
  def _from_dict(cls, data):
      return cls(
          *[data.get(fld.name)
            for fld in fields(AircraftDataCard)]
      )
  
  @classmethod
  def from_json(cls, path: pathlib.Path) -> 'AircraftDataCard':
    with open(path, 'r') as file:      
      adc_dict = json.loads(file.read())
      # return cls._from_dict(adc_dict)
      return dataclass_from_dict(AircraftDataCard, adc_dict)


@dataclass
class AircraftState:
  adc:    AircraftDataCard
  
  # From scenario.
  weight: float
      
  def get_wing_load(self) -> float:
    return self.weight / self.adc.characteristics.wing_area * 10.0
  
  def get_safe_load(self) -> float:
    return self.adc.stores.combat_weight / self.weight * self.adc.characteristics.combat_safe_load





def main() -> None:
  print('Hello Speedbop!')

  adc = AircraftDataCard.from_json(pathlib.Path("adc/fj-3m.json"))
  print(adc)
  state = AircraftState(adc, weight=17.4)
    
  
  print('Wing load: ', state.get_wing_load())
  print('Safe load: ', state.get_safe_load())



if __name__ == '__main__':
  main()