from pydantic import BaseModel, Field


class Invitee(BaseModel):
    name:str
    email:str | None= None


class RouteOption(BaseModel):
    mode:str
    duration_minutes:int
    summary:str
    transfer:int=0
    source:str="mock"

class ItineraryItem(BaseModel):
    start_time: str
    activity: str
    place_name: str | None = None
    notes: str | None = None


class LocalOutingPlan(BaseModel):
    title: str
    date: str
    destination: str
    origin: str
    weather_summary: str
    route: RouteOption
    itinerary: list[ItineraryItem]
    notes: list[str] = Field(default_factory=list)


class TravelSessionState(BaseModel):
    thread_id:str
    raw_goal:str | None = None
    destination:str | None = None
    date_text:str | None = None
    date:str | None = None
    origin:str | None = None
    weather_summary:str | None = None
    weather_ok:bool | None = None
    route_options: list[RouteOption] = Field(default_factory=list)
    selected_route: RouteOption | None = None
    latest_plan: LocalOutingPlan | None = None


